"""Baseline models that run today without training data.

They encode the deck's planning defaults and simple statistics, and they are
the fallback whenever a trained artifact is missing.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Dict, Optional

from ..engine.catalog import BETA, GAMMA, RETURN_PRIOR_STRENGTH, RTO_PRIOR_STRENGTH, Product
from ..engine.demand import penalty


class PriorDemand:
    """Constant price sensitivity + look-alike penalty, shrunk toward a learned β when one exists.

    β_used = w · β_learned + (1 − w) · β_prior, with w = n / (n + n₀) and n₀ = 4 / (Δ² τ²):
    the more price-varied observations a category has, the more its own β counts.
    """
    name, version = "prior_demand", "baseline-1"

    def __init__(self, learned: Optional[Dict[str, dict]] = None):
        self.learned = learned or {}      # category -> {"beta": float, "n": int, "n0": float}

    def elasticity(self, p: Product) -> float:
        L = self.learned.get(p.category)
        if not L:
            return BETA
        w = L["n"] / (L["n"] + L.get("n0", 25))
        return w * L["beta"] + (1 - w) * BETA

    def orders_per_day(self, p: Product, price: float) -> float:
        b = self.elasticity(p)
        return p.ref_orders * math.pow(price / p.ref_price, b) * penalty(price, p.median, GAMMA) / penalty(p.ref_price, p.median, GAMMA)


class BetaBinomialReturnRisk:
    """Category prior updated with this product's matured orders (Beta-binomial).

    Prior strength: the return prior is worth ~58 orders, the RTO prior ~245.
    posterior rate = (m · prior + events) / (m + n)
    """
    name, version = "beta_binomial_returns", "baseline-1"

    def __init__(self, category_rates: Optional[Dict[str, dict]] = None):
        self.category_rates = category_rates or {}

    def rates(self, p: Product, matured_orders: int = 0, returns: int = 0, rto: int = 0) -> dict:
        cat = self.category_rates.get(p.category, {})
        r0 = cat.get("ret", p.ret) / 100
        o0 = cat.get("rto", p.rto) / 100
        mr, mo = RETURN_PRIOR_STRENGTH, RTO_PRIOR_STRENGTH
        r = (mr * r0 + returns) / (mr + matured_orders)
        o = (mo * o0 + rto) / (mo + matured_orders)
        return {"return_rate": round(r * 100, 2), "rto_rate": round(o * 100, 2),
                "evidence_orders": matured_orders, "prior_weight_returns": round(mr / (mr + matured_orders), 2),
                "prior_weight_rto": round(mo / (mo + matured_orders), 2)}


class CatalogueLookalike:
    """Uses the band stored on the product (filled by the catalogue pipeline).

    Production version: CLIP image + title embeddings and attribute filters over
    the live catalogue, returning p25 / median / p75 of the nearest listings.
    """
    name, version = "stored_band", "baseline-1"

    def similar(self, p: Product, k: int = 30) -> dict:
        return {"band": p.band, "median": p.median, "count": p.lookalikes, "crowded": p.lookalikes >= 10,
                "source": "stored on the product (demo)"}


def load_json(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None
