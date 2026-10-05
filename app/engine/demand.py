"""Baseline demand model: constant price sensitivity plus a look-alike penalty.

    orders(p) = orders_ref × (p / p_ref)^β × penalty(p) / penalty(p_ref)

β = −3 is the starting guess for a listing in a crowded feed. Above the
look-alike median, buyers compare and drop off faster, which the penalty
exp(−γ((p − median)/median)²) captures. The trained model that replaces this
plugs in through app.ml (see app/ml/interfaces.py).
"""
from __future__ import annotations

import math
from typing import Optional

from .catalog import GAMMA, Product
from .floor import FloorResult, floor_for


def penalty(p: float, median: float, gamma: float = GAMMA) -> float:
    return math.exp(-gamma * ((p - median) / median) ** 2) if p > median else 1.0


def demand(p: Product, price: float, kink: bool = True, beta: Optional[float] = None) -> float:
    """Orders per day at `price`, at full speed."""
    L = p.ref_price
    b = p.beta if beta is None else beta
    k0 = 1.0 if not kink else penalty(price, p.median) / penalty(L, p.median)
    return p.ref_orders * math.pow(price / L, b) * k0


def profit_per_day(p: Product, price: float, kink: bool = True, f: Optional[FloorResult] = None) -> float:
    f = f or floor_for(p)
    return demand(p, price, kink) * f.k * (price - f.F)


def confidence_from_views(views: float) -> str:
    return "High" if views >= 5000 else ("Medium" if views >= 1000 else "Low")
