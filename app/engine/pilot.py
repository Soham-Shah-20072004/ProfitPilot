"""Pilot design and impact maths (all impact numbers are ILLUSTRATIVE targets)."""
from __future__ import annotations

import hashlib
import math
from typing import Dict, Iterable, List

from .fmt import to_fixed

DESIGNS = {
    "pilot_50_50": {"holdout_share": 0.50, "note": "Pilot, weeks 1–12: 250 vs 250 sellers; detects ₹10 per kept order."},
    "steady_95_5": {"holdout_share": 0.05, "note": "After go: a permanent 5% holdout of sellers."},
}

CITIES = {
    "Surat · ethnic wear": {"sellers": 300, "returns_pct": 18, "scores": [5, 5, 3, 4, 5]},
    "Rajkot · home & kitchen": {"sellers": 200, "returns_pct": 9, "scores": [4, 5, 4, 4, 3]},
}
CITY_WEIGHTS = [0.30, 0.25, 0.15, 0.15, 0.15]   # sellers, fit, Tier 2/3, ops, returns upside


def sample_size(z_alpha: float = 1.96, z_beta: float = 0.84, sigma: float = 40, delta: float = 10) -> dict:
    """Sellers needed per group: n = 2 (z_α/2 + z_β)² σ² ÷ δ²."""
    n = 2 * (z_alpha + z_beta) ** 2 * sigma ** 2 / delta ** 2
    return {"n_per_group": math.ceil(n - 1e-9), "exact": round(n, 1), "unit": "sellers",
            "formula": f"2 × ({z_alpha} + {z_beta})² × {sigma}² ÷ {delta}² = {to_fixed(n, 1)}",
            "pilot_has": 250, "enough": 250 >= n - 1}


def detectable_delta(n_treatment: int, n_holdout: int, z_alpha: float = 1.96, z_beta: float = 0.84, sigma: float = 40) -> float:
    """Smallest lift (₹ per kept order) a design can detect: (z_α/2 + z_β) · σ · √(1/n_T + 1/n_H).
    500 sellers with a 5% holdout (475 vs 25) detect only about ₹23; a 50/50 pilot (250 vs 250) detects ₹10."""
    return (z_alpha + z_beta) * sigma * math.sqrt(1 / n_treatment + 1 / n_holdout)


def lift(treated: float, holdout: float) -> dict:
    return {"lift_pct": round((treated - holdout) / holdout * 100, 1) if holdout else None,
            "formula": f"({treated} − {holdout}) ÷ {holdout}"}


def profit_waterfall() -> dict:
    steps = [("Holdout baseline", 84.0), ("Below-floor / under-priced SKUs fixed", 10.0),
             ("Growth steps +4% on 30% of SKUs", 4.5), ("Panic cuts avoided", 6.0)]
    total = sum(v for _, v in steps)
    return {"steps": [{"label": k, "value": v} for k, v in steps], "result": total,
            "lift_pct": round((total - 84) / 84 * 100, 1),
            "note": "Dual price is not counted: the no-return price is lower by about the return cost it saves, "
                    "so profit per kept order stays the same. Its gain shows up in kept orders and faster cash."}


def kept_orders_chain() -> dict:
    factors = [("listing fixes", 1.10), ("dual price", 1.08), ("price-step elasticity", 0.964),
               ("kept-rate gain", 1.03), ("repricing up", 0.93)]
    v, rows = 28.0, [{"label": "Holdout", "value": 28.0}]
    for label, x in factors:
        v *= x
        rows.append({"label": f"× {x} {label}", "factor": x, "value": round(v, 1)})
    return {"rows": rows, "result": round(v, 1), "rounded": round(v), "lift_pct": round((v - 28) / 28 * 100, 1),
            "note": "0.93 = repricing up: about 25% of products are lifted to their floor or into the band and lose about 28% "
                    "of their orders (1 − 0.25 × 0.28 = 0.93)."}


def meesho_wide() -> dict:
    return {"seller_profit_pct": "+18–30%", "gmv_pct": "+7–11% (upper bound: part of it is sales moving between sellers)",
            "cohort": "₹65 cr → ₹90 cr", "condition": "if all sellers in the cohort adopt"}


def city_scores() -> List[dict]:
    out = []
    for name, c in CITIES.items():
        s = sum(w * x for w, x in zip(CITY_WEIGHTS, c["scores"]))
        out.append({"city": name, "score": round(s, 2), "sellers": c["sellers"], "returns_pct": c["returns_pct"]})
    return out


def assign(seller_ids: Iterable[str], design: str = "pilot_50_50", salt: str = "profitpilot-pilot-1") -> Dict[str, str]:
    """Deterministic seller-level randomisation: every product of a seller is in one group,
    so every buyer always sees one price."""
    share = DESIGNS[design]["holdout_share"]
    out = {}
    for sid in seller_ids:
        h = int(hashlib.sha256(f"{salt}:{sid}".encode()).hexdigest()[:12], 16) / float(16 ** 12)
        out[sid] = "holdout" if h < share else "treatment"
    return out


STOP_RULES = [
    {"if": "Floor accuracy off > ±5% vs settlement", "then": "recalibrate costs"},
    {"if": "Acceptance < 20%", "then": "redesign cards"},
    {"if": "Buyer conversion > 5% below holdout at a similar price", "then": "pause Autopilot"},
    {"if": "Look-alike prices converge (herding)", "then": "widen dispersion"},
]
