"""Request bodies (responses are plain JSON documented in docs/API.md and at /docs)."""
from __future__ import annotations

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field

Mode = Literal["cash", "growth", "margin", "clear"]
Category = Literal["ethnic", "kitchen", "beauty", "kids", "decor"]


class FloorIn(BaseModel):
    category: Category = Field(description="sets the priors for returns, RTO and other costs")
    cs: float = Field(ge=0, description="sourcing cost per piece, ₹")
    pack: float = Field(10, ge=0, description="packaging per order, ₹")
    fwd: float = Field(25, ge=0, description="forward shipping per order, ₹")
    target: float = Field(60, ge=0, description="target profit per kept order T, ₹")
    ret: Optional[float] = Field(None, ge=0, le=60, description="return rate %, default = category prior")
    rto: Optional[float] = Field(None, ge=0, le=60, description="RTO rate %, default = category prior")
    model_config = {"json_schema_extra": {"example": {"category": "ethnic", "cs": 180, "pack": 10, "fwd": 25, "target": 60, "ret": 12, "rto": 8}}}


class FirstPriceIn(FloorIn):
    band: List[int] = Field(default_factory=lambda: [329, 429], min_length=2, max_length=2, description="look-alike p25, p75")
    median: int = 379
    lookalikes: int = 24
    rival_price: int = 299
    offline_price: Optional[int] = None
    model_config = {"json_schema_extra": {"example": {"category": "ethnic", "cs": 180, "pack": 10, "fwd": 25, "target": 60,
                                                      "ret": 12, "rto": 8, "band": [329, 429], "median": 379, "lookalikes": 24, "rival_price": 299}}}


class SaveFirstPriceIn(BaseModel):
    category: Optional[Category] = None
    cs: Optional[float] = Field(None, ge=0)
    pack: Optional[float] = Field(None, ge=0)
    fwd: Optional[float] = Field(None, ge=0)
    target: Optional[float] = Field(None, ge=0)
    ret: Optional[float] = Field(None, ge=0, le=60)
    rto: Optional[float] = Field(None, ge=0, le=60)
    offer_both_prices: bool = True
    seller_guess_return_cost: Optional[float] = Field(None, description="what the seller thought returns + RTO cost per order")


class ProductIn(BaseModel):
    name: str
    emoji: str = "📦"
    category: Category
    offline_price: int = Field(gt=0)
    cs: float = Field(ge=0)
    pack: float = 10
    fwd: float = 25
    target: float = 60
    ret: Optional[float] = None
    rto: Optional[float] = None
    band: List[int] = Field(min_length=2, max_length=2)
    median: int
    lookalikes: int = 10
    rival_price: Optional[int] = None
    expected_orders_per_day: float = Field(5, gt=0, description="orders/day at the start price, at full speed")
    life_days: int = 180


class ProductPatch(BaseModel):
    control: Optional[Literal["man", "cp", "au"]] = None
    cs: Optional[float] = Field(None, ge=0)
    pack: Optional[float] = Field(None, ge=0)
    fwd: Optional[float] = Field(None, ge=0)
    target: Optional[float] = Field(None, ge=0)
    ret: Optional[float] = Field(None, ge=0, le=60)
    rto: Optional[float] = Field(None, ge=0, le=60)
    band: Optional[List[int]] = None
    median: Optional[int] = None
    lookalikes: Optional[int] = None
    rival_price: Optional[int] = None


class SellerPatch(BaseModel):
    goal_mode: Optional[Mode] = None
    language: Optional[Literal["en", "hi"]] = None
    name: Optional[str] = None


class DecisionIn(BaseModel):
    key: str = Field(description="card key from the recommendation, e.g. kurti:growth:369>384:up")
    decision: Literal["y", "n"]
    product_id: Optional[str] = None
    kind: Literal["up", "down", "dual", "hold", "info"] = "info"
    from_price: Optional[int] = None
    to_price: Optional[int] = None
    consent: bool = Field(False, description="seller consented to go below the floor (Exit stage only)")
    card: Optional[dict] = Field(None, description="snapshot of the card the seller saw (headline + Why), kept for the history")


class MetricWeekIn(BaseModel):
    week_start: str = Field(description="ISO date of the week's Monday")
    impressions: int = Field(ge=0)
    clicks: int = Field(ge=0)
    orders: int = Field(ge=0)
    delivered: int = Field(0, ge=0)
    returns: int = Field(0, ge=0)
    rto: int = Field(0, ge=0)
    kept: int = Field(0, ge=0)
    stock_units: Optional[int] = None
    avg_price: Optional[float] = None
    delivery_days: Optional[float] = None


class MetricsIn(BaseModel):
    weeks: List[MetricWeekIn]


class PeersIn(BaseModel):
    impressions_median: float = 4000
    ctr_median: float = 4.0
    ctr_mad: float = 0.53
    cvr_median: float = 3.0
    cvr_mad: float = 0.6
    returns_pct: float = 12
    rto_pct: float = 8
    delivery_days_p75: float = 6


class WeekIn(BaseModel):
    impressions: int
    clicks: int
    orders: int
    returns_pct: float
    rto_pct: float


class DiagnoseIn(BaseModel):
    product_id: str = "kurti"
    weeks: List[WeekIn] = Field(min_length=1, max_length=8, description="last weeks, oldest first (the last two are judged)")
    peers: PeersIn = PeersIn()
    price: int
    band: List[int] = Field(min_length=2, max_length=2)
    stock_days: float
    delivery_days: float
    stock_units: Optional[float] = None
    reorder_point: Optional[float] = None


class ExperimentIn(BaseModel):
    product_id: str
    mode: Optional[Mode] = None
    seed: int = 1
    holdout_price: Optional[int] = None
    kind: Literal["simulation", "live"] = "simulation"


class SimulateIn(BaseModel):
    days: int = Field(1, ge=1, le=365)


class OutcomeIn(BaseModel):
    price: int = Field(description="easy-returns price of the menu that was live")
    impressions: int = Field(ge=0)
    kept: int = Field(ge=0)


class SampleSizeIn(BaseModel):
    z_alpha: float = 1.96
    z_beta: float = 0.84
    sigma: float = Field(40, gt=0)
    delta: float = Field(10, gt=0)


class LiftIn(BaseModel):
    treated: float = 104.5
    holdout: float = 84


class AssignIn(BaseModel):
    seller_ids: List[str]
    design: Literal["pilot_50_50", "steady_95_5"] = "pilot_50_50"


class ReorderIn(BaseModel):
    daily_demand: float = 12
    lead_time_days: float = 7
    sd_per_day: float = 4.1
    z: float = 1.65


class PoolIn(BaseModel):
    commitments: List[int] = Field(default_factory=lambda: [80, 70, 60, 50, 40])
    sellers_committed: Optional[int] = None
    minimum_order: int = 300
    solo_price: int = 210
    pooled_price: int = 188


class CreditIn(BaseModel):
    avg_monthly_payout: float = 60000
    next_stock_order: Optional[float] = None
    return_rate_pct: float = 5


class WeightIn(BaseModel):
    declared_kg: float = 0.5
    scanned_kg: float = 0.62


class CoachIn(BaseModel):
    text: Optional[str] = None
    intent: Optional[str] = None
    product_id: Optional[str] = None


class ClockIn(BaseModel):
    days: float = Field(7, ge=0, le=365)
    run_jobs: bool = True


class ConsentIn(BaseModel):
    consent: bool = False


Overrides = Dict[str, float]
