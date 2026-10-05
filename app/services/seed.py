"""Demo data: one seller and the five products from the app (illustrative numbers)."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import delete
from sqlalchemy.orm import Session

from ..engine.catalog import DEMO_SKUS
from ..models import (KV, AuditEvent, CoachMessage, Decision, Experiment, MetricWeek, Notification, PriceChange,
                      Product, ScheduledCheck, Seller)
from .core import audit, now, set_clock_offset

DEMO_SELLER_ID = "demo"


def seed_demo(db: Session) -> bool:
    """Create the demo seller and products if they don't exist. Returns True if anything was created."""
    if db.get(Seller, DEMO_SELLER_ID):
        return False
    t = now(db)
    db.add(Seller(id=DEMO_SELLER_ID, name="Ramesh (demo seller)", language="en", goal_mode="growth", wins=0, created_at=t))
    for i, (sku, d) in enumerate(DEMO_SKUS.items()):
        sig = dict(d["signals"])
        made = t + timedelta(microseconds=i)      # keeps the app's product order in listings
        db.add(Product(
            id=sku, seller_id=DEMO_SELLER_ID, name=d["name"], emoji=d["emoji"], category=d["category"],
            offline_price=d["offline_price"], ref_price=d["ref_price"], ref_orders=d["ref_orders"], live_price=d["ref_price"],
            cs=d["cs"], pack=d["pack"], fwd=d["fwd"], ret=d["ret"], rto=d["rto"], target=d["target"],
            band_lo=d["band"][0], band_hi=d["band"][1], median=d["median"], lookalikes=d["lookalikes"],
            rival_price=d["rival_price"], recovery_floor=d["recovery_floor"], life_days=d["life_days"],
            signals=sig, control=d["control"], no_return_lead=False,
            launched_at=t - timedelta(days=sig["day"]), last_move_at=t - timedelta(days=d["days_since_move"]),
            created_at=made, updated_at=made))
    audit(db, DEMO_SELLER_ID, "seed_demo", detail={"products": list(DEMO_SKUS)}, at=t)
    db.commit()
    return True


def reset_demo(db: Session) -> None:
    """Wipe all demo state (decisions, price changes, checks, experiments, logs) and seed again."""
    for model in (ScheduledCheck, Decision, PriceChange, Experiment, MetricWeek, Notification, CoachMessage, AuditEvent):
        db.execute(delete(model))
    db.execute(delete(Product).where(Product.seller_id == DEMO_SELLER_ID))
    db.execute(delete(Seller).where(Seller.id == DEMO_SELLER_ID))
    db.execute(delete(KV))
    set_clock_offset(db, 0)
    db.commit()
    seed_demo(db)
