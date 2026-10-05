"""Operations API: scheduled checks, demo clock (time travel), audit log, notifications, reset."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..models import AuditEvent, Notification, PriceChange, Product, ScheduledCheck, Seller
from ..services.core import clock_offset, now, set_clock_offset
from ..services.jobs import run_due
from ..services.seed import reset_demo
from .deps import current_seller, require_key
from .schemas import ClockIn

router = APIRouter()


@router.get("/jobs", tags=["operations"])
def list_jobs(status: str = "all", db: Session = Depends(get_db), seller: Seller = Depends(current_seller)):
    """Auto-revert checks: day 14 (judge on orders) and day 28 (confirm on kept orders)."""
    q = (select(ScheduledCheck, PriceChange, Product).join(PriceChange, ScheduledCheck.price_change_id == PriceChange.id)
         .join(Product, PriceChange.product_id == Product.id).where(Product.seller_id == seller.id))
    if status != "all":
        q = q.where(ScheduledCheck.status == status)
    out = []
    for c, pc, p in db.execute(q.order_by(ScheduledCheck.due_at)).all():
        out.append({"id": c.id, "kind": c.kind, "status": c.status, "due_at": c.due_at.isoformat(),
                    "product_id": p.id, "product": f"{p.emoji} {p.name}", "move": f"₹{pc.from_price} → ₹{pc.to_price}",
                    "price_change_status": pc.status, "result": c.result, "ran_at": c.ran_at.isoformat() if c.ran_at else None})
    return {"now": now(db).isoformat(), "checks": out}


@router.post("/jobs/run-due", tags=["operations"], dependencies=[Depends(require_key)])
def run_due_jobs(db: Session = Depends(get_db)):
    """Run every check that is due now (the background loop also does this every minute)."""
    return {"now": now(db).isoformat(), "ran": run_due(db)}


@router.get("/admin/clock", tags=["operations"])
def get_clock(db: Session = Depends(get_db)):
    return {"now": now(db).isoformat(), "offset_days": clock_offset(db), "demo_clock_enabled": get_settings().demo_clock}


@router.post("/admin/clock/advance", tags=["operations"], dependencies=[Depends(require_key)])
def advance_clock(body: ClockIn, db: Session = Depends(get_db)):
    """Demo time travel: move the server clock forward (cooldowns, undo windows and day-14/28 checks follow it)."""
    if not get_settings().demo_clock:
        raise HTTPException(403, "demo clock disabled (PP_DEMO_CLOCK=false)")
    set_clock_offset(db, clock_offset(db) + body.days)
    db.commit()
    ran = run_due(db) if body.run_jobs else []
    return {"now": now(db).isoformat(), "offset_days": clock_offset(db), "ran": ran}


@router.post("/admin/reset", tags=["operations"], dependencies=[Depends(require_key)])
def reset(db: Session = Depends(get_db)):
    """Wipe demo state and reseed the demo seller with its five products."""
    reset_demo(db)
    return {"reset": True, "now": now(db).isoformat()}


@router.get("/audit", tags=["operations"])
def audit_log(limit: int = Query(50, ge=1, le=500), db: Session = Depends(get_db), seller: Seller = Depends(current_seller)):
    """Every decision, price change, check and input change, newest first."""
    q = select(AuditEvent).where((AuditEvent.seller_id == seller.id) | (AuditEvent.seller_id.is_(None)))
    return [{"id": a.id, "at": a.created_at.isoformat(), "action": a.action, "product_id": a.product_id, "detail": a.detail}
            for a in db.scalars(q.order_by(AuditEvent.id.desc()).limit(limit))]


@router.get("/notifications", tags=["operations"])
def notifications(limit: int = Query(20, ge=1, le=200), db: Session = Depends(get_db), seller: Seller = Depends(current_seller)):
    """Weekly WhatsApp cards (simulated outbox: nothing is sent)."""
    q = select(Notification).where(Notification.seller_id == seller.id).order_by(Notification.id.desc()).limit(limit)
    return [{"id": n.id, "week": n.week_key, "channel": n.channel, "product_id": n.product_id, "title": n.title, "body": n.body,
             "at": n.created_at.isoformat()} for n in db.scalars(q)]
