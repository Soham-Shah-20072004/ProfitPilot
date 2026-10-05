"""Scheduled jobs: auto-revert checks (day 14 / day 28) and the weekly card outbox.

Day 14 judges a move on orders: if profit per impression at the new price is
worse than at the old one, the price goes back automatically. Day 28 confirms
the move on kept orders, once returns have matured. When weekly metrics have
been posted for the product (POST /products/{id}/metrics) the checks use the
observed numbers; otherwise they use the demand model and say so.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..engine.demand import demand
from ..engine.floor import floor_for
from ..engine.fmt import inr, pct
from ..engine.recommend import recommend
from ..models import MetricWeek, Notification, PriceChange, Product, ScheduledCheck, Seller
from .core import audit, now, to_engine


def _observed_ppi(db: Session, p: Product, start: datetime, end: datetime, price: int, F: int) -> Optional[float]:
    rows = db.scalars(select(MetricWeek).where(MetricWeek.product_id == p.id, MetricWeek.week_start >= start.date(),
                                               MetricWeek.week_start < end.date())).all()
    imp = sum(r.impressions for r in rows)
    if not rows or imp == 0:
        return None
    return sum(r.kept for r in rows) * (price - F) / imp


def run_due(db: Session, t: Optional[datetime] = None) -> List[dict]:
    t = t or now(db)
    out = []
    due = db.scalars(select(ScheduledCheck).where(ScheduledCheck.status == "pending", ScheduledCheck.due_at <= t)
                     .order_by(ScheduledCheck.due_at)).all()
    for c in due:
        pc = db.get(PriceChange, c.price_change_id)
        p = db.get(Product, pc.product_id)
        if pc.status != "live":
            c.status, c.ran_at = "cancelled", t
            continue
        e = to_engine(db, p, t)
        f = floor_for(e)
        before_obs = _observed_ppi(db, p, pc.created_at - timedelta(days=28), pc.created_at, pc.from_price, f.F)
        after_obs = _observed_ppi(db, p, pc.created_at, c.due_at, pc.to_price, f.F)
        if before_obs is not None and after_obs is not None:
            before, after, basis = before_obs, after_obs, "observed weekly metrics"
        else:  # no metrics posted yet: compare expected profit per day (impressions assumed unchanged)
            before = demand(e, pc.from_price) * f.k * (pc.from_price - f.F)
            after = demand(e, pc.to_price) * f.k * (pc.to_price - f.F)
            basis = "demand model estimate (no weekly metrics posted yet)"
        change = (after - before) / abs(before) * 100 if before else 0
        if c.kind == "judge_day14":
            if after < before:
                p.live_price = pc.from_price
                p.last_move_at = t
                p.updated_at = t
                pc.status, pc.closed_at, pc.note = "reverted", t, "auto-revert at day 14: profit per impression fell"
                for other in db.scalars(select(ScheduledCheck).where(ScheduledCheck.price_change_id == pc.id,
                                                                      ScheduledCheck.status == "pending", ScheduledCheck.id != c.id)):
                    other.status = "cancelled"
                verdict = f"Reverted {inr(pc.to_price)} → {inr(pc.from_price)}: profit per impression {pct(change, 1)}."
            else:
                verdict = f"Kept {inr(pc.to_price)}: profit per impression {pct(change, 1)} on orders. Confirm on kept orders at day 28."
        else:
            pc.status, pc.closed_at = "confirmed", t
            verdict = f"Confirmed {inr(pc.to_price)} on kept orders (returns matured): {pct(change, 1)}."
        c.status, c.ran_at = "done", t
        c.result = {"verdict": verdict, "before": round(before, 4), "after": round(after, 4), "change_pct": round(change, 1),
                    "basis": basis}
        audit(db, p.seller_id, f"check_{c.kind}", p.id, c.result, t)
        out.append({"check_id": c.id, "kind": c.kind, "product_id": p.id, **c.result})
    db.commit()
    out_cards = weekly_cards(db, t)
    return out + out_cards


def weekly_cards(db: Session, t: Optional[datetime] = None) -> List[dict]:
    """One weekly WhatsApp card per product (simulated outbox; nothing is sent)."""
    t = t or now(db)
    iso = t.isocalendar()
    week_key = f"{iso[0]}-W{iso[1]:02d}"
    made = []
    for s in db.scalars(select(Seller)):
        if db.scalars(select(Notification).where(Notification.seller_id == s.id, Notification.week_key == week_key)).first():
            continue
        for p in db.scalars(select(Product).where(Product.seller_id == s.id)):
            r = recommend(to_engine(db, p, t), s.goal_mode)
            body = f"{r['h']}. {r.get('sub') or ''} Reply YES / NO, or tap WHY in the app.".replace("  ", " ")
            db.add(Notification(seller_id=s.id, product_id=p.id, channel="whatsapp", week_key=week_key,
                                title=f"{p.emoji} {p.name}: weekly check", body=body, created_at=t))
            made.append({"kind": "weekly_card", "product_id": p.id, "week": week_key, "headline": r["h"]})
    db.commit()
    return made
