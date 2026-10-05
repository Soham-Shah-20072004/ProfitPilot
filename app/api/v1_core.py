"""Core API: health, bootstrap, seller, products, floor / first price, recommendations, decisions."""
from __future__ import annotations

import re
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import __version__
from ..config import get_settings
from ..db import get_db
from ..engine.catalog import CATEGORIES, MODES, Product as EProduct, Signals
from ..engine.floor import compute_floor, floor_for
from ..engine.pricing import first_price, profit_curve, simulate
from ..engine.recommend import recommend
from ..models import Decision, Product, Seller
from ..services.core import audit, now, product_out, to_engine
from ..services.decisions import DecisionError, active_state, decide, decision_out, undo
from .deps import current_seller, own_product, require_key
from .schemas import DecisionIn, FirstPriceIn, FloorIn, ProductIn, ProductPatch, SaveFirstPriceIn, SellerPatch

router = APIRouter()


# ---------------------------------------------------------------- meta
@router.get("/health", tags=["meta"])
def health(db: Session = Depends(get_db)):
    """Liveness + identity check (the app uses `service == 'profitpilot'` to detect the server)."""
    db.execute(select(1))
    s = get_settings()
    return {"status": "ok", "service": "profitpilot", "version": __version__,
            "database": "postgresql" if s.database_url.startswith("postgres") else "sqlite",
            "server_time": now(db).isoformat(), "illustrative_data": True, "ai": _ai_brief()}


def _ai_brief() -> dict:
    from ..ai.provider import ai_status
    st = ai_status()
    return {"active": st["active"], "provider": st["provider"], "model": st["model"]}


@router.get("/categories", tags=["meta"])
def categories():
    """Category priors used when a seller has no history of their own."""
    return CATEGORIES


@router.get("/modes", tags=["meta"])
def modes():
    """Goal modes: same floor, different objective."""
    return MODES


@router.get("/bootstrap", tags=["meta"])
def bootstrap(db: Session = Depends(get_db), seller: Seller = Depends(current_seller)):
    """Everything the app needs on load: seller, products with live state, active decisions."""
    t = now(db)
    prods = db.scalars(select(Product).where(Product.seller_id == seller.id).order_by(Product.created_at, Product.id)).all()
    return {"seller": _seller_out(seller), "products": [product_out(db, p, t) for p in prods],
            **active_state(db, seller), "server_time": t.isoformat()}


# ---------------------------------------------------------------- seller
def _seller_out(s: Seller) -> dict:
    return {"id": s.id, "name": s.name, "language": s.language, "goal_mode": s.goal_mode, "wins": s.wins,
            "autopilot_unlocked": s.wins >= 4, "pilot_group": s.pilot_group}


@router.get("/seller", tags=["seller"])
def get_seller(seller: Seller = Depends(current_seller)):
    return _seller_out(seller)


@router.patch("/seller", tags=["seller"], dependencies=[Depends(require_key)])
def patch_seller(body: SellerPatch, db: Session = Depends(get_db), seller: Seller = Depends(current_seller)):
    """Change goal mode (cash / growth / margin / clear), language or name."""
    changed = body.model_dump(exclude_none=True)
    for k, v in changed.items():
        setattr(seller, k, v)
    if changed:
        audit(db, seller.id, "seller_updated", detail=changed)
    db.commit()
    return _seller_out(seller)


# ---------------------------------------------------------------- products
@router.get("/products", tags=["products"])
def list_products(db: Session = Depends(get_db), seller: Seller = Depends(current_seller)):
    t = now(db)
    prods = db.scalars(select(Product).where(Product.seller_id == seller.id).order_by(Product.created_at, Product.id)).all()
    return [product_out(db, p, t) for p in prods]


@router.post("/products", tags=["products"], status_code=201, dependencies=[Depends(require_key)])
def create_product(body: ProductIn, db: Session = Depends(get_db), seller: Seller = Depends(current_seller)):
    """Add a new listing. The floor and the first price are computed immediately."""
    t = now(db)
    f = compute_floor(body.category, body.cs, body.pack, body.fwd, body.target, body.ret, body.rto)
    slug = re.sub(r"[^a-z0-9]+", "-", body.name.lower()).strip("-")[:40] or "product"
    pid = f"{slug}-{uuid.uuid4().hex[:6]}"
    cat = CATEGORIES[body.category]
    p = Product(id=pid, seller_id=seller.id, name=body.name, emoji=body.emoji, category=body.category,
                offline_price=body.offline_price, ref_price=f.start_price, ref_orders=body.expected_orders_per_day,
                live_price=f.start_price, cs=body.cs, pack=body.pack, fwd=body.fwd,
                ret=cat["ret"] if body.ret is None else body.ret, rto=cat["rto"] if body.rto is None else body.rto,
                target=body.target, band_lo=body.band[0], band_hi=body.band[1], median=body.median,
                lookalikes=body.lookalikes, rival_price=body.rival_price or body.band[0], recovery_floor=f.F - 1,
                life_days=body.life_days, signals={"stage": "launch", "views": 0, "cvr": 0, "cvr_med": 0, "cvr_weeks": 0,
                                                   "doi": 45, "rival": 0, "g": 0, "ctr": 0},
                control="man", no_return_lead=False, launched_at=t, last_move_at=None, created_at=t, updated_at=t)
    db.add(p)
    audit(db, seller.id, "product_created", pid, {"F": f.F, "start_price": f.start_price}, t)
    db.commit()
    return {"product": product_out(db, p, t), "first_price": first_price(to_engine(db, p, t))}


@router.get("/products/{product_id}", tags=["products"])
def get_product(product_id: str, db: Session = Depends(get_db), seller: Seller = Depends(current_seller)):
    return product_out(db, own_product(product_id, db, seller))


@router.patch("/products/{product_id}", tags=["products"], dependencies=[Depends(require_key)])
def patch_product(product_id: str, body: ProductPatch, db: Session = Depends(get_db), seller: Seller = Depends(current_seller)):
    """Change control mode (man / cp / au), costs or market data."""
    p = own_product(product_id, db, seller)
    changed = body.model_dump(exclude_none=True)
    if changed.get("control") == "au" and seller.wins < 4:
        raise HTTPException(409, f"Autopilot unlocks after 4 accepted wins ({seller.wins}/4)")
    band = changed.pop("band", None)
    if band:
        p.band_lo, p.band_hi = band
    for k, v in changed.items():
        setattr(p, k, v)
    p.updated_at = now(db)
    audit(db, seller.id, "product_updated", p.id, {**changed, **({"band": band} if band else {})})
    db.commit()
    return product_out(db, p)


@router.get("/products/{product_id}/floor", tags=["pricing"])
def product_floor(product_id: str, db: Session = Depends(get_db), seller: Seller = Depends(current_seller)):
    """Return-adjusted floor F with the full cost build and its explanation."""
    e = to_engine(db, own_product(product_id, db, seller))
    f = floor_for(e)
    return {**f.to_dict(), "why": f.explain(e.name)}


@router.get("/products/{product_id}/first-price", tags=["pricing"])
def product_first_price(product_id: str, db: Session = Depends(get_db), seller: Seller = Depends(current_seller)):
    return first_price(to_engine(db, own_product(product_id, db, seller)))


@router.post("/products/{product_id}/first-price", tags=["pricing"], dependencies=[Depends(require_key)])
def save_first_price(product_id: str, body: SaveFirstPriceIn, db: Session = Depends(get_db),
                     seller: Seller = Depends(current_seller)):
    """Save the seller's cost inputs from the First-price flow and return the server's floor and prices."""
    p = own_product(product_id, db, seller)
    changed = body.model_dump(exclude_none=True, exclude={"offer_both_prices", "seller_guess_return_cost"})
    for k, v in changed.items():
        setattr(p, k, v)
    t = now(db)
    p.updated_at = t
    e = to_engine(db, p, t)
    fp = first_price(e)
    audit(db, seller.id, "first_price_saved", p.id, {"inputs": changed, "F": fp["floor"]["F"], "start_price": fp["start_price"],
                                                      "no_return_price": fp["no_return_price"], "offer_both": body.offer_both_prices,
                                                      "seller_guess": body.seller_guess_return_cost}, t)
    db.commit()
    return {"product": product_out(db, p, t), "first_price": fp}


@router.get("/products/{product_id}/simulate", tags=["pricing"])
def product_simulate(product_id: str, price: int = Query(..., gt=0), kink: bool = True, db: Session = Depends(get_db),
                     seller: Seller = Depends(current_seller)):
    """What happens at this price: orders, kept orders, profit per kept order and per day, Loss Warning."""
    return simulate(to_engine(db, own_product(product_id, db, seller)), price, kink)


@router.get("/products/{product_id}/profit-curve", tags=["pricing"])
def product_curve(product_id: str, kink: bool = True, db: Session = Depends(get_db), seller: Seller = Depends(current_seller)):
    return profit_curve(to_engine(db, own_product(product_id, db, seller)), kink=kink)


@router.post("/floor", tags=["pricing"])
def floor_stateless(body: FloorIn):
    """Compute a floor from raw inputs (no product needed)."""
    f = compute_floor(body.category, body.cs, body.pack, body.fwd, body.target, body.ret, body.rto)
    return {**f.to_dict(), "why": f.explain()}


@router.post("/first-price", tags=["pricing"])
def first_price_stateless(body: FirstPriceIn):
    """First price for a product that is not listed yet (no history)."""
    cat = CATEGORIES[body.category]
    f = compute_floor(body.category, body.cs, body.pack, body.fwd, body.target, body.ret, body.rto)
    e = EProduct(id="draft", name="Draft product", emoji="📦", category=body.category,
                 offline_price=body.offline_price or f.start_price, ref_price=f.start_price, ref_orders=5,
                 live_price=f.start_price, cs=body.cs, pack=body.pack, fwd=body.fwd,
                 ret=cat["ret"] if body.ret is None else body.ret, rto=cat["rto"] if body.rto is None else body.rto,
                 target=body.target, band=body.band, median=body.median, lookalikes=body.lookalikes,
                 rival_price=body.rival_price, recovery_floor=f.F - 1, life_days=180, signals=Signals(stage="launch"))
    return first_price(e)


# ---------------------------------------------------------------- recommendations
@router.get("/recommendations", tags=["recommendations"])
def recommendations(mode: Optional[str] = None, db: Session = Depends(get_db), seller: Seller = Depends(current_seller)):
    """This week's card for every product (Yes / No / Why), for the seller's goal mode or `mode`."""
    m = mode or seller.goal_mode
    if m not in MODES:
        raise HTTPException(422, f"unknown mode {m!r}")
    t = now(db)
    prods = db.scalars(select(Product).where(Product.seller_id == seller.id).order_by(Product.created_at, Product.id)).all()
    hist = active_state(db, seller)["history"]
    applied = {v["id"] for v in hist.values()}
    return [{**recommend(to_engine(db, p, t), m), "applied": p.id in applied} for p in prods]


@router.get("/products/{product_id}/recommendation", tags=["recommendations"])
def product_recommendation(product_id: str, mode: Optional[str] = None, db: Session = Depends(get_db),
                           seller: Seller = Depends(current_seller)):
    m = mode or seller.goal_mode
    return recommend(to_engine(db, own_product(product_id, db, seller)), m)


# ---------------------------------------------------------------- decisions
def _err(e: DecisionError):
    raise HTTPException(e.status, {"message": e.message, "checks": e.checks})


@router.get("/decisions", tags=["decisions"])
def list_decisions(include_undone: bool = False, db: Session = Depends(get_db), seller: Seller = Depends(current_seller)):
    q = select(Decision).where(Decision.seller_id == seller.id)
    if not include_undone:
        q = q.where(Decision.undone_at.is_(None))
    return [decision_out(d) for d in db.scalars(q.order_by(Decision.id.desc()))]


@router.post("/decisions", tags=["decisions"], dependencies=[Depends(require_key)])
def post_decision(body: DecisionIn, db: Session = Depends(get_db), seller: Seller = Depends(current_seller)):
    """YES / NO on a card. A YES on a price move is re-checked against the guardrails before it is published;
    if a check fails the response is 409 with the failed checks."""
    try:
        return decide(db, seller, body.key, body.decision, body.product_id, body.kind, body.from_price, body.to_price,
                      body.card, body.consent)
    except DecisionError as e:
        db.rollback()
        _err(e)


@router.post("/decisions/{key}/undo", tags=["decisions"], dependencies=[Depends(require_key)])
def undo_decision(key: str, db: Session = Depends(get_db), seller: Seller = Depends(current_seller)):
    """Undo within 24 hours: the old price comes back and the scheduled checks are cancelled."""
    try:
        return undo(db, seller, key)
    except DecisionError as e:
        db.rollback()
        _err(e)


@router.delete("/decisions/{key}", tags=["decisions"], dependencies=[Depends(require_key)])
def delete_decision(key: str, db: Session = Depends(get_db), seller: Seller = Depends(current_seller)):
    """Withdraw a NO or an info decision ('Reconsider'). Price moves use /undo."""
    try:
        return undo(db, seller, key)
    except DecisionError as e:
        db.rollback()
        _err(e)
