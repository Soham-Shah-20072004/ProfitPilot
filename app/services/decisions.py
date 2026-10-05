"""Yes / No / Undo on recommendation cards, with server-side guardrails.

A YES on a price move is re-checked here (floor, step ≤ 8%, 7-day cooldown,
≤ 2 moves a month, views sanity check) before anything is published. Accepted
moves get a 24-hour undo window and two scheduled checks: day 14 (judge on
orders) and day 28 (confirm on kept orders, once returns have matured).
"""
from __future__ import annotations

from datetime import timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..engine.catalog import CONFIRM_DAY, JUDGE_DAY, UNDO_WINDOW_HOURS
from ..engine.floor import floor_for
from ..engine.pricing import preflight, preflight_ok
from ..engine.recommend import recommend
from ..models import Decision, PriceChange, Product, ScheduledCheck, Seller
from .core import audit, now, product_out, to_engine


class DecisionError(Exception):
    def __init__(self, status: int, message: str, checks: Optional[list] = None):
        super().__init__(message)
        self.status, self.message, self.checks = status, message, checks


def active_decision(db: Session, seller_id: str, key: str) -> Optional[Decision]:
    q = select(Decision).where(Decision.seller_id == seller_id, Decision.key == key, Decision.undone_at.is_(None))
    return db.scalars(q.order_by(Decision.id.desc())).first()


def _pc_out(pc: Optional[PriceChange]) -> Optional[dict]:
    if not pc:
        return None
    return {"id": pc.id, "product_id": pc.product_id, "from_price": pc.from_price, "to_price": pc.to_price, "kind": pc.kind,
            "status": pc.status, "source": pc.source, "created_at": pc.created_at.isoformat(), "undo_until": pc.undo_until.isoformat(),
            "closed_at": pc.closed_at.isoformat() if pc.closed_at else None, "note": pc.note}


def decision_out(d: Decision) -> dict:
    return {"key": d.key, "decision": d.decision, "kind": d.kind, "product_id": d.product_id, "from_price": d.from_price,
            "to_price": d.to_price, "price_change_id": d.price_change_id, "created_at": d.created_at.isoformat(),
            "undone_at": d.undone_at.isoformat() if d.undone_at else None}


def _own_product(db: Session, seller: Seller, product_id: Optional[str]) -> Product:
    p = db.get(Product, product_id) if product_id else None
    if not p or p.seller_id != seller.id:
        raise DecisionError(404, f"product {product_id!r} not found")
    return p


def decide(db: Session, seller: Seller, key: str, decision: str, product_id: Optional[str] = None, kind: str = "info",
           from_price: Optional[int] = None, to_price: Optional[int] = None, card: Optional[dict] = None,
           consent: bool = False) -> dict:
    if decision not in ("y", "n"):
        raise DecisionError(422, "decision must be 'y' or 'n'")
    t = now(db)
    existing = active_decision(db, seller.id, key)
    if existing:
        if existing.decision == decision:
            p = db.get(Product, existing.product_id) if existing.product_id else None
            return {"decision": decision_out(existing), "product": product_out(db, p, t) if p else None,
                    "price_change": _pc_out(db.get(PriceChange, existing.price_change_id)) if existing.price_change_id else None,
                    "idempotent": True}
        if existing.price_change_id:
            raise DecisionError(409, "this card already changed the price; undo it first")
        existing.undone_at = t

    pc, checks_out, server_rec, product = None, [], None, None
    if decision == "y" and kind in ("up", "down", "dual"):
        product = _own_product(db, seller, product_id)
        if from_price is not None and product.live_price != from_price:
            raise DecisionError(409, f"stale card: the live price is now ₹{product.live_price}, not ₹{from_price}")
        e = to_engine(db, product, t)
        f = floor_for(e)
        server_rec = recommend(e, seller.goal_mode, f)
        if kind in ("up", "down"):
            if to_price is None:
                raise DecisionError(422, "to_price is required for a price move")
            checks = preflight(e, product.live_price, to_price, f, consent=consent)
            checks_out = checks
            if not preflight_ok(checks):
                failed = [c["k"] for c in checks if not c["ok"]]
                raise DecisionError(409, "blocked by guardrails: " + ", ".join(failed), checks)
            pc = PriceChange(product_id=product.id, decision_key=key, from_price=product.live_price, to_price=to_price, kind=kind,
                             source="card", status="live", prev_last_move_at=product.last_move_at, created_at=t,
                             undo_until=t + timedelta(hours=UNDO_WINDOW_HOURS))
            db.add(pc)
            db.flush()
            product.live_price = to_price
            product.last_move_at = t
            for kname, days in (("judge_day14", JUDGE_DAY), ("confirm_day28", CONFIRM_DAY)):
                db.add(ScheduledCheck(price_change_id=pc.id, kind=kname, due_at=t + timedelta(days=days), status="pending"))
        else:  # dual: lead with the no-return price; the easy-returns price stays
            pn = product.live_price - f.gap
            pc = PriceChange(product_id=product.id, decision_key=key, from_price=product.live_price, to_price=pn, kind="dual",
                             source="card", status="live", prev_last_move_at=product.last_move_at, created_at=t,
                             undo_until=t + timedelta(hours=UNDO_WINDOW_HOURS), note="no-return price leads; easy-returns stays on")
            db.add(pc)
            db.flush()
            product.no_return_lead = True
        product.updated_at = t
        seller.wins += 1
        audit(db, seller.id, "price_change" if kind != "dual" else "dual_price_lead", product.id,
              {"key": key, "from": pc.from_price, "to": pc.to_price, "kind": kind}, t)
    elif product_id:
        product = db.get(Product, product_id)

    d = Decision(seller_id=seller.id, key=key, product_id=product_id, decision=decision, kind=kind,
                 from_price=from_price, to_price=to_price, card=card, price_change_id=pc.id if pc else None, created_at=t)
    db.add(d)
    if not pc:
        audit(db, seller.id, "decision", product_id, {"key": key, "decision": decision, "kind": kind}, t)
    db.commit()
    agrees = None
    if server_rec is not None:
        agrees = (server_rec["kind"] == kind and server_rec["to"] == (to_price if kind != "dual" else server_rec["to"]))
    return {"decision": decision_out(d), "product": product_out(db, product, t) if product else None,
            "price_change": _pc_out(pc), "checks": checks_out,
            "server_recommendation": {"key": server_rec["key"], "kind": server_rec["kind"], "from": server_rec["from"],
                                      "to": server_rec["to"], "headline": server_rec["h"]} if server_rec else None,
            "server_agrees": agrees}


def undo(db: Session, seller: Seller, key: str) -> dict:
    t = now(db)
    d = active_decision(db, seller.id, key)
    if not d:
        raise DecisionError(404, "no active decision with this key")
    product = db.get(Product, d.product_id) if d.product_id else None
    if d.price_change_id:
        pc = db.get(PriceChange, d.price_change_id)
        if pc.status != "live":
            raise DecisionError(409, f"this price change is already {pc.status}")
        if t > pc.undo_until:
            raise DecisionError(409, "the 24-hour undo window has passed; the day-14 check will decide")
        if pc.kind in ("up", "down"):
            product.live_price = pc.from_price
            product.last_move_at = pc.prev_last_move_at
        else:
            product.no_return_lead = False
        product.updated_at = t
        pc.status, pc.closed_at = "undone", t
        for c in db.scalars(select(ScheduledCheck).where(ScheduledCheck.price_change_id == pc.id,
                                                          ScheduledCheck.status == "pending")):
            c.status = "cancelled"
        seller.wins = max(0, seller.wins - 1)
        audit(db, seller.id, "undo", product.id, {"key": key, "restored": pc.from_price}, t)
    else:
        audit(db, seller.id, "decision_withdrawn", d.product_id, {"key": key}, t)
    d.undone_at = t
    db.commit()
    return {"undone": key, "product": product_out(db, product, t) if product else None}


def active_state(db: Session, seller: Seller) -> dict:
    """What the app needs to restore: active decisions and still-undoable applied cards."""
    t = now(db)
    decisions, history = {}, {}
    for d in db.scalars(select(Decision).where(Decision.seller_id == seller.id, Decision.undone_at.is_(None))):
        decisions[d.key] = d.decision
        if d.price_change_id:
            pc = db.get(PriceChange, d.price_change_id)
            if pc and pc.status == "live" and t <= pc.undo_until:
                history[d.key] = {"id": d.product_id, "from": pc.from_price,
                                  "to": pc.to_price, "kind": pc.kind, "card": d.card or {},
                                  "undo_until": pc.undo_until.isoformat()}
    return {"decisions": decisions, "history": history}
