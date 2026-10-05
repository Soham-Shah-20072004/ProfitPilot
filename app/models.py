"""Database tables (SQLAlchemy 2.0). Works on SQLite and PostgreSQL."""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class Seller(Base):
    __tablename__ = "sellers"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    language: Mapped[str] = mapped_column(String(8), default="en")
    goal_mode: Mapped[str] = mapped_column(String(16), default="growth")
    wins: Mapped[int] = mapped_column(Integer, default=0)            # accepted suggestions (Autopilot unlocks at 4)
    pilot_group: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    products: Mapped[list["Product"]] = relationship(back_populates="seller", cascade="all, delete-orphan")


class Product(Base):
    __tablename__ = "products"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    seller_id: Mapped[str] = mapped_column(ForeignKey("sellers.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    emoji: Mapped[str] = mapped_column(String(16), default="📦")
    category: Mapped[str] = mapped_column(String(32))
    offline_price: Mapped[int] = mapped_column(Integer)
    ref_price: Mapped[int] = mapped_column(Integer)          # demand anchor: ref_orders were seen at this price
    ref_orders: Mapped[float] = mapped_column(Float)         # orders/day at ref_price, at full speed
    live_price: Mapped[int] = mapped_column(Integer)
    cs: Mapped[float] = mapped_column(Float)
    pack: Mapped[float] = mapped_column(Float)
    fwd: Mapped[float] = mapped_column(Float)
    ret: Mapped[float] = mapped_column(Float)
    rto: Mapped[float] = mapped_column(Float)
    target: Mapped[float] = mapped_column(Float)
    band_lo: Mapped[int] = mapped_column(Integer)
    band_hi: Mapped[int] = mapped_column(Integer)
    median: Mapped[int] = mapped_column(Integer)
    lookalikes: Mapped[int] = mapped_column(Integer)
    rival_price: Mapped[int] = mapped_column(Integer)
    recovery_floor: Mapped[int] = mapped_column(Integer)
    life_days: Mapped[int] = mapped_column(Integer)
    signals: Mapped[dict] = mapped_column(JSON, default=dict)
    control: Mapped[str] = mapped_column(String(8), default="cp")   # man | cp | au
    no_return_lead: Mapped[bool] = mapped_column(Boolean, default=False)
    launched_at: Mapped[datetime] = mapped_column(DateTime)
    last_move_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime)
    seller: Mapped[Seller] = relationship(back_populates="products")


class PriceChange(Base):
    __tablename__ = "price_changes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    decision_key: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    from_price: Mapped[int] = mapped_column(Integer)
    to_price: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(16))             # up | down | dual
    source: Mapped[str] = mapped_column(String(16), default="card")
    status: Mapped[str] = mapped_column(String(16), default="live")   # live | undone | reverted | confirmed
    prev_last_move_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    undo_until: Mapped[datetime] = mapped_column(DateTime)
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class Decision(Base):
    __tablename__ = "decisions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    seller_id: Mapped[str] = mapped_column(ForeignKey("sellers.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(200), index=True)
    product_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    decision: Mapped[str] = mapped_column(String(2))          # y | n
    kind: Mapped[str] = mapped_column(String(16), default="info")   # up | down | dual | hold | info
    from_price: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    to_price: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    card: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    price_change_id: Mapped[Optional[int]] = mapped_column(ForeignKey("price_changes.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    undone_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class ScheduledCheck(Base):
    __tablename__ = "scheduled_checks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    price_change_id: Mapped[int] = mapped_column(ForeignKey("price_changes.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(24))             # judge_day14 | confirm_day28
    due_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")   # pending | done | cancelled
    result: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    ran_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class Experiment(Base):
    __tablename__ = "experiments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    mode: Mapped[str] = mapped_column(String(16))
    seed: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(16), default="simulation")   # simulation | live
    state: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime)


class MetricWeek(Base):
    """Weekly funnel per product: the input for the stage classifier and Diagnose in production."""
    __tablename__ = "metric_weeks"
    __table_args__ = (UniqueConstraint("product_id", "week_start"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    week_start: Mapped[date] = mapped_column(Date)
    impressions: Mapped[int] = mapped_column(Integer, default=0)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    orders: Mapped[int] = mapped_column(Integer, default=0)
    delivered: Mapped[int] = mapped_column(Integer, default=0)
    returns: Mapped[int] = mapped_column(Integer, default=0)
    rto: Mapped[int] = mapped_column(Integer, default=0)
    kept: Mapped[int] = mapped_column(Integer, default=0)
    stock_units: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    avg_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    delivery_days: Mapped[Optional[float]] = mapped_column(Float, nullable=True)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    seller_id: Mapped[Optional[str]] = mapped_column(String(64), index=True, nullable=True)
    product_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    action: Mapped[str] = mapped_column(String(48))
    detail: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, index=True)


class Notification(Base):
    """Outbox for the weekly WhatsApp card (simulated: nothing is sent)."""
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    seller_id: Mapped[str] = mapped_column(String(64), index=True)
    product_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    channel: Mapped[str] = mapped_column(String(16), default="whatsapp")
    week_key: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime)


class CoachMessage(Base):
    __tablename__ = "coach_messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    seller_id: Mapped[str] = mapped_column(String(64), index=True)
    product_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    text: Mapped[str] = mapped_column(Text)
    intent: Mapped[Optional[str]] = mapped_column(String(24), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)


class KV(Base):
    __tablename__ = "settings_kv"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
