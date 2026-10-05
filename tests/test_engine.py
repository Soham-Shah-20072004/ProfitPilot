"""Engine numbers that the deck and the app quote."""
import pytest

from app.engine import inventory, pilot
from app.engine.bandit import new_experiment, simulate_days, summary
from app.engine.catalog import demo_product
from app.engine.coach import route
from app.engine.diagnose import demo_cases, diagnose
from app.engine.floor import compute_floor, floor_for
from app.engine.fmt import ceil9, floor9, inr, js_round, pct
from app.engine.lifecycle import classify_stage, rival_test
from app.engine.pricing import first_price, preflight


def test_js_rounding_and_formatting():
    assert js_round(2.5) == 3 and js_round(-2.5) == -2 and js_round(13.99) == 14
    assert ceil9(339.48) == 349 and floor9(378) == 369
    assert inr(136800) == "₹1,36,800" and inr(-22) == "−₹22"
    assert pct(4) == "+4%" and pct(-6.53, 1) == "−6.5%"


def test_kurti_floor_build():
    f = floor_for(demo_product("kurti"))
    assert (f.F, f.k, f.c_ret, f.c_rto, f.other) == (309, 0.78, 32, 18, 44)
    assert (f.start_price, f.no_return_price, f.floor_no_return) == (369, 339, 277)
    assert (f.safety_margin, f.safe_minimum) == (18, 327)
    assert f.start_price - f.safe_minimum == 42


def test_floor_defaults_to_category_priors():
    f = compute_floor("ethnic", 180, 10, 25, 60)
    assert f.F == 309 and f.ret == 12 and f.rto == 8


def test_first_price_market_checks():
    fp = first_price(demo_product("kurti"))
    assert fp["market"]["position"] == "competitive" and fp["market"]["velocity_price"] == 369
    assert fp["warning"] is None
    expensive = demo_product("kurti")
    expensive.cs = 320
    assert first_price(expensive)["warning"] is not None


def test_guardrails():
    p = demo_product("kurti")
    ok = {c["k"]: c["ok"] for c in preflight(p, 369, 384)}
    assert all(ok.values())
    bad = {c["k"]: c["ok"] for c in preflight(p, 369, 299, dsm=3, mpm=2)}
    assert not bad["Floor"] and not bad["Step ≤ 8%"] and not bad["Cooldown 7 days"] and not bad["≤ 2 moves / month"]


def test_rival_hold_beats_match_for_kurti():
    p = demo_product("kurti")
    rt = rival_test(p, 384, 369)
    assert rt["decision"] == "hold"
    assert round(rt["profit_day_hold"]) == 819 and round(rt["profit_day_match"]) == 739
    assert rt["orders_if_match"] == 15.8


def test_stage_classifier():
    grow = classify_stage(60, [100, 100, 100, 100, 130, 130, 130, 130])
    assert grow["stage"] == "growth" and grow["g"] == 30.0
    assert classify_stage(10, [])["stage"] == "launch"
    assert classify_stage(90, [100] * 8, stock_units=700, avg_daily_units=10)["stage"] == "decline"
    assert classify_stage(200, [100] * 8, stock_age_days=120)["stage"] == "exit"


@pytest.mark.parametrize("case,expected", [("cat", "clicks"), ("price", "price"), ("conv", "conv"), ("ret", "ret"),
                                           ("rto", "rto"), ("inv", "stock"), ("ful", "del")])
def test_diagnose_cases(case, expected):
    r = diagnose(demo_cases()[case])
    assert r["bottleneck"] == expected
    assert r["price_move_allowed"] == (expected == "price")


def test_bandit_menus_and_floor():
    p = demo_product("kurti")
    st = new_experiment(p, "growth", seed=1)
    assert [a.p for a in st.arms] == [299, 369, 399, 429]          # ₹339 is the no-return half of the ₹369 menu
    assert [a.pn for a in st.arms] == [269, 339, 369, 399]
    simulate_days(p, st, 30)
    s = summary(p, st)
    assert s["days_per_menu"]["299"] == 0                             # below the floor: never shown
    assert max(s["days_per_menu"], key=s["days_per_menu"].get) == "369"
    assert sum(s["days_per_menu"].values()) == 30                     # one menu per day


def test_pilot_maths():
    assert pilot.sample_size()["n_per_group"] == 251
    assert round(pilot.detectable_delta(475, 25)) == 23 and round(pilot.detectable_delta(250, 250)) == 10
    assert pilot.profit_waterfall()["result"] == 104.5
    assert pilot.kept_orders_chain()["result"] == 30.7
    groups = pilot.assign([f"s{i}" for i in range(500)])
    assert 200 < sum(1 for g in groups.values() if g == "holdout") < 300
    assert [c["score"] for c in pilot.city_scores()] == [4.55, 4.1]


def test_v2_modules():
    rop = inventory.reorder_point()
    assert (rop["reorder_point"], rop["order_quantity"]) == (102, 170)
    pool = inventory.pooled_procurement()
    assert pool["total"] == 300 and pool["unit_price"] == 188
    assert inventory.pooled_procurement(sellers_committed=3)["unit_price"] == 210
    assert inventory.credit_limit()["limit"] == 34000
    assert inventory.weight_audit(0.5, 0.62)["extra_per_order"] == 20


def test_coach_routing():
    assert route("Mera product nahi bik raha") == "nosell"
    assert route("My orders dropped") == "orders"
    assert route("phir bhi price cut karna hai") == "cut"
    assert route("hello") is None
