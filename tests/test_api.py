"""API flows: bootstrap, YES with guardrails, undo, time travel checks, engines."""
K = "kurti:growth:369>384:up"


def yes(client, key=K, frm=369, to=384, kind="up"):
    return client.post("/api/v1/decisions", json={"key": key, "decision": "y", "product_id": "kurti", "kind": kind,
                                                    "from_price": frm, "to_price": to, "card": {"h": "Step up"}})


def test_health_and_bootstrap(client):
    h = client.get("/api/v1/health").json()
    assert h["service"] == "profitpilot" and h["status"] == "ok"
    b = client.get("/api/v1/bootstrap").json()
    ids = {p["id"]: p for p in b["products"]}
    assert set(ids) == {"kurti", "lunch", "serum", "romper", "vase"}
    assert ids["kurti"]["floor"]["F"] == 309 and ids["kurti"]["live_price"] == 369
    assert b["seller"]["goal_mode"] == "growth" and b["decisions"] == {}


def test_recommendations_match_the_app(client):
    recs = {r["id"]: r["key"] for r in client.get("/api/v1/recommendations").json()}
    assert recs["kurti"] == K and recs["serum"] == "serum:growth:249>259:up" and recs["vase"] == "vase:growth:499>469:down"


def test_yes_applies_price_and_schedules_checks(client):
    r = yes(client)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["product"]["live_price"] == 384 and body["server_agrees"] is True
    jobs = client.get("/api/v1/jobs").json()["checks"]
    assert sorted(c["kind"] for c in jobs) == ["confirm_day28", "judge_day14"]
    b = client.get("/api/v1/bootstrap").json()
    assert b["decisions"][K] == "y" and K in b["history"]
    assert yes(client).json().get("idempotent") is True


def test_guardrails_block_second_move_and_below_floor(client):
    yes(client)
    r = yes(client, key="kurti:growth:384>399:up", frm=384, to=399)
    assert r.status_code == 409 and "Cooldown" in r.json()["detail"]["message"]
    client.post("/api/v1/admin/clock/advance", json={"days": 8, "run_jobs": False})
    r = yes(client, key="kurti:clear:384>299:down", frm=384, to=299, kind="down")
    assert r.status_code == 409
    failed = [c["k"] for c in r.json()["detail"]["checks"] if not c["ok"]]
    assert "Floor" in failed and "Step ≤ 8%" in failed


def test_stale_card_rejected(client):
    r = yes(client, frm=359, to=374)
    assert r.status_code == 409 and "stale" in r.json()["detail"]["message"]


def test_undo_within_24h_and_not_after(client):
    yes(client)
    u = client.post(f"/api/v1/decisions/{K}/undo")
    assert u.status_code == 200 and u.json()["product"]["live_price"] == 369
    assert all(c["status"] == "cancelled" for c in client.get("/api/v1/jobs").json()["checks"])
    yes(client)
    client.post("/api/v1/admin/clock/advance", json={"days": 2, "run_jobs": False})
    late = client.post(f"/api/v1/decisions/{K}/undo")
    assert late.status_code == 409


def test_day14_and_day28_checks(client):
    yes(client)
    r = client.post("/api/v1/admin/clock/advance", json={"days": 14}).json()
    v = [x for x in r["ran"] if x.get("kind") == "judge_day14"]
    assert v and v[0]["verdict"].startswith("Kept ₹384")
    r = client.post("/api/v1/admin/clock/advance", json={"days": 14}).json()
    assert any(x.get("kind") == "confirm_day28" for x in r["ran"])
    assert client.get("/api/v1/products/kurti").json()["live_price"] == 384
    assert len(client.get("/api/v1/notifications").json()) >= 5


def test_auto_revert_when_profit_falls(client):
    # a raise on the serum to 268 (+7.6%) is worse per day than 249 at β = −3 near the median
    client.post("/api/v1/admin/clock/advance", json={"days": 1, "run_jobs": False})
    r = client.post("/api/v1/decisions", json={"key": "serum:margin:249>268:up", "decision": "y", "product_id": "serum",
                                               "kind": "up", "from_price": 249, "to_price": 268})
    assert r.status_code == 200, r.text
    out = client.post("/api/v1/admin/clock/advance", json={"days": 14}).json()
    v = [x for x in out["ran"] if x.get("kind") == "judge_day14"][0]
    assert v["verdict"].startswith("Reverted")
    assert client.get("/api/v1/products/serum").json()["live_price"] == 249


def test_mode_and_control(client):
    assert client.patch("/api/v1/seller", json={"goal_mode": "cash"}).json()["goal_mode"] == "cash"
    recs = {r["id"]: r for r in client.get("/api/v1/recommendations").json()}
    assert recs["kurti"]["kind"] == "dual" and recs["kurti"]["to"] == 339
    assert client.patch("/api/v1/products/kurti", json={"control": "au"}).status_code == 409   # needs 4 wins


def test_first_price_save_and_stateless(client):
    r = client.post("/api/v1/products/kurti/first-price", json={"cs": 180, "target": 60}).json()
    assert r["first_price"]["start_price"] == 369 and r["first_price"]["safe_minimum"] == 327
    s = client.post("/api/v1/first-price", json={"category": "ethnic", "cs": 180, "pack": 10, "fwd": 25, "target": 60}).json()
    assert s["no_return_price"] == 339
    f = client.post("/api/v1/floor", json={"category": "ethnic", "cs": 180}).json()
    assert f["F"] == 309 and len(f["why"]) == 5


def test_create_product(client):
    r = client.post("/api/v1/products", json={"name": "Cotton dupatta", "category": "ethnic", "offline_price": 249, "cs": 90,
                                              "band": [199, 299], "median": 249, "lookalikes": 18})
    assert r.status_code == 201
    pid = r.json()["product"]["id"]
    assert client.get(f"/api/v1/products/{pid}/recommendation").json()["h"].startswith("Launch")


def test_engines(client):
    lc = client.get("/api/v1/products/kurti/lifecycle").json()
    assert lc["rival_test"]["decision"] == "hold" and lc["story"]["markdowns"] == [359, 339, 319]
    assert client.post("/api/v1/diagnose/cases/inv/run").json()["bottleneck"] == "stock"
    e = client.post("/api/v1/experiments", json={"product_id": "kurti", "seed": 1}).json()
    s = client.post(f"/api/v1/experiments/{e['id']}/simulate", json={"days": 30}).json()
    assert s["day"] == 30 and s["days_per_menu"]["299"] == 0
    live = client.post("/api/v1/experiments", json={"product_id": "kurti", "seed": 3, "kind": "live"}).json()
    menu = client.post(f"/api/v1/experiments/{live['id']}/choose").json()["menu"]
    after = client.post(f"/api/v1/experiments/{live['id']}/outcomes",
                        json={"price": menu["easy_returns"], "impressions": 4500, "kept": 17}).json()
    assert after["day"] == 1
    assert client.post("/api/v1/pilot/sample-size", json={}).json()["n_per_group"] == 251
    assert client.get("/api/v1/pilot/impact").json()["profit_per_kept_order"]["result"] == 104.5
    assert client.post("/api/v1/v2/credit", json={}).json()["limit"] == 34000
    assert client.post("/api/v1/coach/ask", json={"text": "kitna profit hoga"}).json()["intent"] == "profit"
    assert client.get("/api/v1/models").json()["models"][1]["slot"] == "demand"


def test_metrics_reclassify_stage(client):
    weeks = [{"week_start": f"2026-0{m}-0{d}", "impressions": 4000, "clicks": 160, "orders": 20, "kept": k}
             for (m, d), k in zip([(6, 1), (6, 8), (6, 9), (7, 1), (7, 2), (7, 3), (7, 4), (7, 5)],
                                  [100, 100, 100, 100, 70, 70, 70, 70])]
    r = client.post("/api/v1/products/vase/metrics", json={"weeks": weeks}).json()
    assert r["reclassified"] and r["classification"]["stage"] == "decline"


def test_frontend_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "ProfitPilot" in r.text
