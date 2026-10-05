"""Engine API: lifecycle, metrics + diagnose, price-search experiments, pilot maths, 2.0 modules, coach, models."""
from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..engine import bandit, inventory, pilot
from ..engine.coach import CHIPS, RULES, answer, route
from ..engine.diagnose import DiagnoseInput, Peers, Week, demo_cases, diagnose
from ..engine.floor import floor_for
from ..engine.lifecycle import classify_stage, exit_options, life_story, markdown_ladder, rival_test, stage_on_day, price_on_day
from ..engine.recommend import recommend
from ..ml import registry
from ..models import CoachMessage, Experiment, MetricWeek, Product, Seller
from ..services.core import audit, now, to_engine
from .deps import current_seller, own_product, require_key
from .schemas import (AssignIn, CoachIn, CreditIn, DiagnoseIn, ExperimentIn, LiftIn, MetricsIn, OutcomeIn, PoolIn,
                      ReorderIn, SampleSizeIn, SimulateIn, WeightIn)

router = APIRouter()


# ---------------------------------------------------------------- lifecycle
@router.get("/products/{product_id}/lifecycle", tags=["lifecycle"])
def lifecycle(product_id: str, db: Session = Depends(get_db), seller: Seller = Depends(current_seller)):
    """Current stage, this week's lifecycle card, the rival test, markdown ladder, decline exits and the simulated life story."""
    p = own_product(product_id, db, seller)
    e = to_engine(db, p)
    f = floor_for(e)
    story = life_story(e)
    day = min(e.signals.day, e.life_days)
    rt = rival_test(e, story["P1"], story["P0"], f, story["rival"])
    return {"product_id": p.id, "stage": e.signals.stage, "age_days": e.signals.day,
            "card": recommend(e, seller.goal_mode, f),
            "rival_test": rt, "markdown_ladder": markdown_ladder(e.live_price, f), "exits": exit_options(f),
            "story": {**story, "today": {"day": day, "stage": stage_on_day(story, day), "price": price_on_day(story, day)}},
            "hygiene": [
                {"rule": "Max step", "value": "8% per move"}, {"rule": "Min data", "value": "1,000 views = sanity check"},
                {"rule": "Cooldown", "value": "7 days per SKU"}, {"rule": "Stability", "value": "≤ 2 price moves / month"},
                {"rule": "Floor", "value": "never below F"}, {"rule": "Auto-revert", "value": "judge day 14 · confirm day 28"}]}


@router.post("/products/{product_id}/metrics", tags=["metrics"], dependencies=[Depends(require_key)])
def post_metrics(product_id: str, body: MetricsIn, db: Session = Depends(get_db), seller: Seller = Depends(current_seller)):
    """Ingest weekly funnel metrics (upsert by week). With 8+ weeks the stage is re-classified from kept units."""
    p = own_product(product_id, db, seller)
    for w in body.weeks:
        ws = date.fromisoformat(w.week_start)
        row = db.scalars(select(MetricWeek).where(MetricWeek.product_id == p.id, MetricWeek.week_start == ws)).first()
        data = w.model_dump(exclude={"week_start"})
        if row:
            for k, v in data.items():
                setattr(row, k, v)
        else:
            db.add(MetricWeek(product_id=p.id, week_start=ws, **data))
    db.flush()
    rows = db.scalars(select(MetricWeek).where(MetricWeek.product_id == p.id).order_by(MetricWeek.week_start)).all()
    t = now(db)
    result = {"weeks_stored": len(rows), "reclassified": False}
    if len(rows) >= 8:
        last = rows[-1]
        daily = sum(r.kept for r in rows[-4:]) / 28 or None
        cls = classify_stage(max(0, (t - p.launched_at).days), [r.kept for r in rows], last.stock_units, daily)
        sig = dict(p.signals or {})
        sig.update({"stage": cls["stage"], "g": cls["g"] if cls["g"] is not None else sig.get("g", 0)})
        if cls["doi"] is not None:
            sig["doi"] = cls["doi"]
        if last.impressions:
            sig["views"] = last.impressions
            sig["ctr"] = round(last.clicks / last.impressions * 100, 2)
        if last.clicks:
            sig["cvr"] = round(last.orders / last.clicks * 100, 2)
        p.signals = sig
        result.update(reclassified=True, classification=cls)
    audit(db, seller.id, "metrics_ingested", p.id, {"weeks": len(body.weeks)}, t)
    db.commit()
    return result


@router.get("/products/{product_id}/metrics", tags=["metrics"])
def get_metrics(product_id: str, db: Session = Depends(get_db), seller: Seller = Depends(current_seller)):
    p = own_product(product_id, db, seller)
    rows = db.scalars(select(MetricWeek).where(MetricWeek.product_id == p.id).order_by(MetricWeek.week_start)).all()
    return [{"week_start": r.week_start.isoformat(), "impressions": r.impressions, "clicks": r.clicks, "orders": r.orders,
             "delivered": r.delivered, "returns": r.returns, "rto": r.rto, "kept": r.kept, "stock_units": r.stock_units,
             "avg_price": r.avg_price, "delivery_days": r.delivery_days} for r in rows]


# ---------------------------------------------------------------- diagnose
def _diag_out(case_id: Optional[str], inp: DiagnoseInput) -> dict:
    return {"case": case_id, **diagnose(inp)}


@router.get("/diagnose/cases", tags=["diagnose"])
def diagnose_cases():
    """The demo cases from the app's Diagnose screen, with their inputs."""
    out = {}
    for k, c in demo_cases().items():
        out[k] = {"product_id": c.product_id, "price": c.price, "band": c.band, "stock_days": c.stock_days,
                  "delivery_days": c.delivery_days, "weeks": [w.__dict__ for w in c.weeks], "peers": c.peers.__dict__}
    return out


@router.post("/diagnose/cases/{case_id}/run", tags=["diagnose"])
def diagnose_case(case_id: str):
    cases = demo_cases()
    if case_id not in cases:
        raise HTTPException(404, f"unknown case {case_id!r}; try {sorted(cases)}")
    return _diag_out(case_id, cases[case_id])


@router.post("/diagnose", tags=["diagnose"])
def diagnose_any(body: DiagnoseIn):
    """Run the 8-signal scan on your own numbers."""
    inp = DiagnoseInput(product_id=body.product_id, weeks=[Week(**w.model_dump()) for w in body.weeks],
                        peers=Peers(**body.peers.model_dump()), price=body.price, band=body.band,
                        stock_days=body.stock_days, delivery_days=body.delivery_days,
                        stock_units=body.stock_units, reorder_point=body.reorder_point)
    return _diag_out(None, inp)


# ---------------------------------------------------------------- price search (bandit)
def _exp_out(db: Session, ex: Experiment) -> dict:
    p = db.get(Product, ex.product_id)
    st = bandit.ExperimentState.from_dict(ex.state)
    out = bandit.summary(to_engine(db, p), st)
    out.update(id=ex.id, kind=ex.kind, created_at=ex.created_at.isoformat(), updated_at=ex.updated_at.isoformat(),
               state={k: v for k, v in ex.state.items() if k != "rng_state"})
    return out


def _own_exp(db: Session, seller: Seller, exp_id: int) -> Experiment:
    ex = db.get(Experiment, exp_id)
    if not ex:
        raise HTTPException(404, "experiment not found")
    own_product(ex.product_id, db, seller)
    return ex


@router.post("/experiments", tags=["price search"], status_code=201, dependencies=[Depends(require_key)])
def create_experiment(body: ExperimentIn, db: Session = Depends(get_db), seller: Seller = Depends(current_seller)):
    """Start a price search over price menus for one product (simulation for the demo, or live)."""
    p = own_product(body.product_id, db, seller)
    mode = body.mode or seller.goal_mode
    st = bandit.new_experiment(to_engine(db, p), mode, body.seed, body.holdout_price)
    t = now(db)
    ex = Experiment(product_id=p.id, mode=mode, seed=body.seed, kind=body.kind, state=st.to_dict(), created_at=t, updated_at=t)
    db.add(ex)
    db.commit()
    return _exp_out(db, ex)


@router.get("/experiments/{exp_id}", tags=["price search"])
def get_experiment(exp_id: int, db: Session = Depends(get_db), seller: Seller = Depends(current_seller)):
    return _exp_out(db, _own_exp(db, seller, exp_id))


@router.post("/experiments/{exp_id}/simulate", tags=["price search"], dependencies=[Depends(require_key)])
def simulate_experiment(exp_id: int, body: SimulateIn, db: Session = Depends(get_db), seller: Seller = Depends(current_seller)):
    """Demo: run the policy for N days against a hidden 'true' demand curve (one menu per day for every buyer)."""
    ex = _own_exp(db, seller, exp_id)
    if ex.kind != "simulation":
        raise HTTPException(409, "live experiments learn from /outcomes, not from simulation")
    p = db.get(Product, ex.product_id)
    st = bandit.simulate_days(to_engine(db, p), bandit.ExperimentState.from_dict(ex.state), body.days)
    ex.state = st.to_dict()
    ex.updated_at = now(db)
    db.commit()
    return _exp_out(db, ex)


@router.post("/experiments/{exp_id}/choose", tags=["price search"], dependencies=[Depends(require_key)])
def choose_menu(exp_id: int, db: Session = Depends(get_db), seller: Seller = Depends(current_seller)):
    """Live: today's menu (one Thompson draw; the same menu for every buyer all day)."""
    ex = _own_exp(db, seller, exp_id)
    p = db.get(Product, ex.product_id)
    st = bandit.ExperimentState.from_dict(ex.state)
    res = bandit.choose_today(to_engine(db, p), st)
    ex.state = st.to_dict()
    db.commit()
    return res


@router.post("/experiments/{exp_id}/outcomes", tags=["price search"], dependencies=[Depends(require_key)])
def post_outcome(exp_id: int, body: OutcomeIn, db: Session = Depends(get_db), seller: Seller = Depends(current_seller)):
    """Live: post the day's impressions and kept orders for the menu that was live."""
    ex = _own_exp(db, seller, exp_id)
    st = bandit.ExperimentState.from_dict(ex.state)
    try:
        bandit.record_outcome(st, body.price, body.impressions, body.kept)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    ex.state = st.to_dict()
    ex.updated_at = now(db)
    db.commit()
    return _exp_out(db, ex)


# ---------------------------------------------------------------- pilot
@router.post("/pilot/sample-size", tags=["pilot"])
def sample_size(body: SampleSizeIn):
    """Sellers needed per group: n = 2 (z_α/2 + z_β)² σ² ÷ δ²."""
    out = pilot.sample_size(body.z_alpha, body.z_beta, body.sigma, body.delta)
    out["detectable_with_5pct_holdout_of_500"] = round(pilot.detectable_delta(475, 25, body.z_alpha, body.z_beta, body.sigma), 1)
    out["detectable_with_250_vs_250"] = round(pilot.detectable_delta(250, 250, body.z_alpha, body.z_beta, body.sigma), 1)
    return out


@router.post("/pilot/lift", tags=["pilot"])
def lift(body: LiftIn):
    return pilot.lift(body.treated, body.holdout)


@router.get("/pilot/impact", tags=["pilot"])
def impact():
    """ILLUSTRATIVE targets with their causal chains (not results)."""
    return {"illustrative": True, "cities": pilot.city_scores(), "designs": pilot.DESIGNS,
            "profit_per_kept_order": pilot.profit_waterfall(), "kept_orders_per_seller_month": pilot.kept_orders_chain(),
            "meesho_wide": pilot.meesho_wide(), "stop_rules": pilot.STOP_RULES,
            "primary_metric": "seller profit per week (normalised per impression) vs holdout sellers"}


@router.post("/pilot/assign", tags=["pilot"], dependencies=[Depends(require_key)])
def assign(body: AssignIn, db: Session = Depends(get_db)):
    """Seller-level randomisation (deterministic). Known sellers get their group saved."""
    groups = pilot.assign(body.seller_ids, body.design)
    for sid, g in groups.items():
        s = db.get(Seller, sid)
        if s:
            s.pilot_group = g
    db.commit()
    return {"design": body.design, "groups": groups,
            "counts": {g: sum(1 for v in groups.values() if v == g) for g in ("treatment", "holdout")}}


# ---------------------------------------------------------------- ProfitPilot 2.0 (opt-in modules)
@router.post("/v2/reorder-point", tags=["2.0 modules"])
def reorder(body: ReorderIn):
    return inventory.reorder_point(body.daily_demand, body.lead_time_days, body.sd_per_day, body.z)


@router.post("/v2/pool", tags=["2.0 modules"])
def pool(body: PoolIn):
    return inventory.pooled_procurement(body.commitments, body.sellers_committed, body.minimum_order, body.solo_price,
                                        body.pooled_price)


@router.post("/v2/credit", tags=["2.0 modules"])
def credit(body: CreditIn):
    return inventory.credit_limit(body.avg_monthly_payout, body.next_stock_order, body.return_rate_pct)


@router.post("/v2/weight-audit", tags=["2.0 modules"])
def weight(body: WeightIn):
    return inventory.weight_audit(body.declared_kg, body.scanned_kg)


@router.get("/v2/packaging-kit/{category}", tags=["2.0 modules"])
def packaging(category: str):
    if category not in inventory.PACK_KITS:
        raise HTTPException(404, f"unknown category {category!r}")
    return inventory.packaging_kit(category)


# ---------------------------------------------------------------- coach
@router.post("/coach/ask", tags=["coach"])
def coach_ask(body: CoachIn, db: Session = Depends(get_db), seller: Seller = Depends(current_seller)):
    """Ask in Hindi or English (or pass an `intent`). Answers come from engine numbers and name their source."""
    intent = body.intent or route(body.text or "")
    pid = body.product_id or "kurti"
    p = db.get(Product, pid)
    if not p or p.seller_id != seller.id:
        p = db.scalars(select(Product).where(Product.seller_id == seller.id)).first()
    if not p:
        raise HTTPException(404, "no products yet")
    t = now(db)
    prods = {x.id: to_engine(db, x, t) for x in db.scalars(select(Product).where(Product.seller_id == seller.id))}
    res = answer(intent, prods[p.id], seller.goal_mode, prods) if intent else answer("", prods[p.id])
    if body.text:
        db.add(CoachMessage(seller_id=seller.id, product_id=p.id, text=body.text[:2000], intent=intent, created_at=t))
        db.commit()
    return res


@router.get("/coach/rules", tags=["coach"])
def coach_rules():
    return {"rules": RULES, "chips": CHIPS}


# ---------------------------------------------------------------- models
@router.get("/models", tags=["models"])
def models_status():
    """Which model serves each slot (baseline vs trained artifact) and what data each planned model needs."""
    return registry.status()


@router.get("/products/{product_id}/model-view", tags=["models"])
def model_view(product_id: str, matured_orders: int = 0, returns: int = 0, rto: int = 0,
               db: Session = Depends(get_db), seller: Seller = Depends(current_seller)):
    """What the model slots say about one product: β in use, demand at a few prices, return-risk posterior, look-alikes."""
    e = to_engine(db, own_product(product_id, db, seller))
    dm, rr, lk = registry.demand_model(), registry.return_risk_model(), registry.lookalike_model()
    f = floor_for(e)
    prices = sorted(set(f.price_points + [e.live_price]))
    return {"demand_model": dm.name, "beta": round(dm.elasticity(e), 3),
            "orders_per_day": {str(x): round(dm.orders_per_day(e, x), 2) for x in prices},
            "return_risk": rr.rates(e, matured_orders, returns, rto), "lookalikes": lk.similar(e)}
