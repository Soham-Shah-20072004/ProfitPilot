"""Return-adjusted floor F: what one KEPT order really costs the seller.

Of 100 placed orders, 3 are cancelled, some are refused at the door (RTO)
and some come back after delivery (returns). Every cost of the failed orders
is spread over the orders that stay sold, so

    F = sourcing + packaging + forward shipping
        + return cost per kept order + RTO cost per kept order + other costs.

Selling below F loses money on every order that stays sold.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

from .catalog import CATEGORIES, DISPATCHED, RETURN_PRIOR_STRENGTH, RTO_PRIOR_STRENGTH, Product
from .fmt import ceil9, inr, js_round


@dataclass
class FloorResult:
    F: int                  # return-adjusted floor per kept order
    k: float                # kept rate (kept / 100 placed)
    kept: int
    delivered: int
    returns_n: int          # returned per 100 placed
    rto_n: int              # refused (RTO) per 100 placed
    c_ret: int              # return cost per kept order
    c_rto: int              # RTO cost per kept order
    other: int              # damage + promo + ads + tax/payment + capital
    other_parts: Dict[str, int]
    cs: float
    pack: float
    fwd: float
    ret: float
    rto: float
    T: float                # target profit per kept order
    start_price: int        # P_easy = F + T
    no_return_price: int    # P_no = P_easy − gap
    gap: int                # ≈ the return cost saved, in ₹10 steps
    floor_no_return: int    # F − C_ret
    k_no_return: float
    B: int                  # return + RTO cost per kept order (inside F)
    safety_margin: int      # uncertainty margin on top of F
    clear_price: int        # F ÷ 0.97 (CLEAR mode)
    margin_price: int       # F ÷ 0.78 (MARGIN mode target)
    growth_price: int
    price_points: List[int] = field(default_factory=list)
    recovery_floor: int = 0

    @property
    def safe_minimum(self) -> int:
        return self.F + self.safety_margin

    def to_dict(self) -> dict:
        d = asdict(self)
        d["safe_minimum"] = self.safe_minimum
        return d

    def explain(self, name: str = "this product") -> List[str]:
        o = self.other_parts
        return [
            f"F = sourcing {inr(self.cs)} + packaging {inr(self.pack)} + forward shipping {inr(self.fwd)} "
            f"+ returns {inr(self.c_ret)} + RTO {inr(self.c_rto)} + other {inr(self.other)} "
            f"(damage {o['dmg']}, promo {o['promo']}, ads {o['ads']}, tax/payment {o['tax']}, capital {o['cap']}) = {inr(self.F)} per kept order.",
            f"Kept rate k = {self.k:.2f}: 100 placed → {DISPATCHED} dispatched → {self.delivered} delivered → {self.kept} kept.",
            f"Return + RTO cost B = {inr(self.c_ret)} + {inr(self.c_rto)} = {inr(self.B)} per kept order (already inside F, not added again).",
            f"Safety margin {inr(self.safety_margin)} covers the return and RTO rates being off by about one standard deviation; "
            f"safe minimum = {inr(self.F)} + {inr(self.safety_margin)} = {inr(self.safe_minimum)}.",
            f"Start price = F + T = {inr(self.F)} + {inr(self.T)} = {inr(self.start_price)}; "
            f"no-return price {inr(self.no_return_price)} (gap {inr(self.gap)} ≈ the {inr(self.c_ret)} return cost saved).",
        ]


def compute_floor(category: str, cs: float, pack: float, fwd: float, T: float,
                  ret: Optional[float] = None, rto: Optional[float] = None,
                  recovery_floor: Optional[int] = None) -> FloorResult:
    """Floor F and the prices built on it. ret / rto default to the category prior."""
    C = CATEGORIES[category]
    ret = C["ret"] if ret is None else ret
    rto = C["rto"] if rto is None else rto
    rto_n = js_round(DISPATCHED * rto / 100)
    delivered = DISPATCHED - rto_n
    returns_n = js_round(delivered * ret / 100)
    kept = delivered - returns_n
    if kept <= 0:
        raise ValueError("return + RTO rates leave no kept orders")
    k = kept / 100
    c_ret = js_round(returns_n * C["u_ret"] / kept)
    c_rto = js_round(rto_n * C["u_rto"] / kept)
    o = C["other"]
    other = o["dmg"] + o["promo"] + o["ads"] + o["tax"] + o["cap"]
    F = js_round(cs + pack + fwd + c_ret + c_rto + other)
    start = js_round(F + T)
    gap = max(10, 10 * js_round(c_ret / 10))
    pn = start - gap
    u = max(10, js_round(start * 0.0813 / 10) * 10)
    points = [pn - u - 10, pn, start, start + u, start + 2 * u]
    sm = js_round(F * math.sqrt((ret / 100) * (1 - ret / 100) / RETURN_PRIOR_STRENGTH
                                + (rto / 100) * (1 - rto / 100) / RTO_PRIOR_STRENGTH) / k)
    rec = F - 1 if recovery_floor is None else min(recovery_floor, F - 1)
    return FloorResult(
        F=F, k=k, kept=kept, delivered=delivered, returns_n=returns_n, rto_n=rto_n,
        c_ret=c_ret, c_rto=c_rto, other=other, other_parts=dict(o),
        cs=cs, pack=pack, fwd=fwd, ret=ret, rto=rto, T=T,
        start_price=start, no_return_price=pn, gap=gap, floor_no_return=F - c_ret,
        k_no_return=delivered / 100, B=c_ret + c_rto, safety_margin=sm,
        clear_price=ceil9(F / 0.97), margin_price=max(start, ceil9(F / 0.78)), growth_price=ceil9(F / 0.92),
        price_points=points, recovery_floor=rec,
    )


def floor_for(p: Product) -> FloorResult:
    return compute_floor(p.category, p.cs, p.pack, p.fwd, p.target, p.ret, p.rto, p.recovery_floor)
