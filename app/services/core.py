"""Shared service helpers: the demo clock, audit log, and DB row ↔ engine mapping."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..engine.catalog import Product as EProduct, Signals
from ..engine.floor import floor_for
from ..models import KV, AuditEvent, PriceChange, Product

CLOCK_KEY = "clock_offset_days"


# ---------------------------------------------------------------- demo clock
def clock_offset(db: Session) -> float:
    row = db.get(KV, CLOCK_KEY)
    return float(row.value) if row else 0.0


def now(db: Session) -> datetime:
    """UTC now plus the demo time-travel offset (naive UTC, as stored in the DB)."""
    real = datetime.now(timezone.utc).replace(tzinfo=None)
    return real + timedelta(days=clock_offset(db))


def set_clock_offset(db: Session, days: float) -> None:
    row = db.get(KV, CLOCK_KEY)
    if row:
        row.value = str(days)
    else:
        db.add(KV(key=CLOCK_KEY, value=str(days)))


# ---------------------------------------------------------------- audit
def audit(db: Session, seller_id: Optional[str], action: str, product_id: Optional[str] = None,
          detail: Optional[dict] = None, at: Optional[datetime] = None) -> None:
    db.add(AuditEvent(seller_id=seller_id, product_id=product_id, action=action, detail=detail or {},
                      created_at=at or now(db)))


# ---------------------------------------------------------------- row ↔ engine
def days_since_move(p: Product, t: datetime) -> int:
    if p.last_move_at is None:
        return 999
    return max(0, int((t - p.last_move_at).total_seconds() // 86400))


def moves_this_month(db: Session, product_id: str, t: datetime) -> int:
    start = t.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    q = select(PriceChange).where(PriceChange.product_id == product_id, PriceChange.created_at >= start,
                                  PriceChange.created_at <= t, PriceChange.kind.in_(["up", "down"]),
                                  PriceChange.status != "undone", PriceChange.source != "auto_revert")
    return len(db.scalars(q).all())


def age_days(p: Product, t: datetime) -> int:
    return max(0, int((t - p.launched_at).total_seconds() // 86400))


def to_engine(db: Session, p: Product, t: Optional[datetime] = None) -> EProduct:
    t = t or now(db)
    sig = dict(p.signals or {})
    sig["day"] = age_days(p, t)
    known = {k: sig[k] for k in Signals.__dataclass_fields__ if k in sig}
    e = EProduct(
        id=p.id, name=p.name, emoji=p.emoji, category=p.category, offline_price=p.offline_price,
        ref_price=p.ref_price, ref_orders=p.ref_orders, live_price=p.live_price,
        cs=p.cs, pack=p.pack, fwd=p.fwd, ret=p.ret, rto=p.rto, target=p.target,
        band=[p.band_lo, p.band_hi], median=p.median, lookalikes=p.lookalikes, rival_price=p.rival_price,
        recovery_floor=p.recovery_floor, life_days=p.life_days, signals=Signals(**known),
        days_since_move=days_since_move(p, t), moves_this_month=moves_this_month(db, p.id, t), control=p.control,
    )
    if get_settings().use_learned_models:
        from ..ml.registry import demand_model
        e.beta = demand_model().elasticity(e)
    return e


def product_out(db: Session, p: Product, t: Optional[datetime] = None) -> dict:
    t = t or now(db)
    e = to_engine(db, p, t)
    f = floor_for(e)
    return {
        "id": p.id, "name": p.name, "emoji": p.emoji, "category": p.category,
        "offline_price": p.offline_price, "ref_price": p.ref_price, "ref_orders_per_day": p.ref_orders,
        "live_price": p.live_price, "no_return_price": p.live_price - f.gap, "no_return_lead": p.no_return_lead,
        "costs": {"cs": p.cs, "pack": p.pack, "fwd": p.fwd, "ret": p.ret, "rto": p.rto, "target": p.target},
        "market": {"band": [p.band_lo, p.band_hi], "median": p.median, "lookalikes": p.lookalikes, "rival_price": p.rival_price},
        "recovery_floor": p.recovery_floor, "life_days": p.life_days,
        "signals": {**(p.signals or {}), "day": e.signals.day},
        "control": p.control,
        "days_since_move": e.days_since_move, "moves_this_month": e.moves_this_month,
        "last_move_at": p.last_move_at.isoformat() if p.last_move_at else None,
        "floor": {"F": f.F, "k": f.k, "safety_margin": f.safety_margin, "safe_minimum": f.safe_minimum,
                  "start_price": f.start_price, "no_return_price": f.no_return_price, "gap": f.gap,
                  "floor_no_return": f.floor_no_return, "B": f.B},
    }
