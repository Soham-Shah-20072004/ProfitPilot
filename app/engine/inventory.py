"""ProfitPilot 2.0 modules (each needs seller opt-in): reorder point, pooled
procurement, working-capital credit, weight audit and packaging kit."""
from __future__ import annotations

import math
from typing import List, Optional

from .fmt import js_round

SLABS = [(0.5, 25), (1.0, 45), (1.5, 62), (2.0, 78), (3.0, 96)]   # weight (kg) → forward freight ₹

PACK_KITS = {
    "ethnic": {"kit": '10×12" printed polybag + tissue', "cost": 4, "current": 6, "damage": 1, "note": "−₹2 vs generic box; damage ~0%"},
    "kitchen": {"kit": "3-ply box, snug fit", "cost": 12, "current": 15, "damage": 3, "note": "−₹3; dents 3% → 1%"},
    "beauty": {"kit": "bubble pouch + seal", "cost": 5, "current": 8, "damage": 2, "note": "leak damage 2% → 0.5%"},
    "kids": {"kit": "soft polybag, 2-pack", "cost": 4, "current": 10, "damage": 3, "note": "−₹6 on twin packs"},
    "decor": {"kit": "honeycomb wrap + double-wall box", "cost": 22, "current": 25, "damage": 16, "note": "breakage 6% → 3% (C_dmg ₹16 → ₹8)"},
}


def reorder_point(daily_demand: float = 12, lead_time_days: float = 7, sd_per_day: float = 4.1, z: float = 1.65) -> dict:
    ss = js_round(z * sd_per_day * math.sqrt(lead_time_days))
    dl = daily_demand * lead_time_days
    rop = dl + ss
    qty = js_round(daily_demand * 14 / 10) * 10
    return {"demand_in_lead_time": dl, "safety_stock": ss, "reorder_point": rop, "order_quantity": qty,
            "rule": "ROP = d × L + z·σ·√L; order ≈ 2 weeks of sales (d × 14, rounded to 10)",
            "why": [f"Demand during lead time = {daily_demand:g} × {lead_time_days:g} = {dl:g}.",
                    f"Safety stock = z·σ·√L = {ss} (service level z {z}).",
                    f"Order quantity ≈ 2 weeks of sales: {daily_demand:g} × 14 = {daily_demand * 14:g} → {qty}."]}


def pooled_procurement(commitments: Optional[List[int]] = None, sellers_committed: Optional[int] = None,
                       minimum_order: int = 300, solo_price: int = 210, pooled_price: int = 188) -> dict:
    allq = commitments or [80, 70, 60, 50, 40]
    n = len(allq) if sellers_committed is None else max(0, min(len(allq), sellers_committed))
    qs = allq[:n]
    total = sum(qs)
    ok = total >= minimum_order
    price = pooled_price if ok else solo_price
    mine = allq[0] if allq else 0
    return {"commitments": qs, "total": total, "minimum_order": minimum_order, "met": ok, "unit_price": price,
            "your_quantity": mine, "your_cost": mine * price, "your_saving": (solo_price - price) * mine,
            "rule": "Meesho ops signs the purchase order only when firm commitments reach the minimum; "
                    "no rival data is shared, the supplier sees only the pooled total.",
            "summary": f"{' + '.join(str(q) for q in qs)} = {total} {'≥' if ok else '<'} minimum order {minimum_order} → "
                       f"₹{solo_price} → ₹{price}"}


def credit_limit(avg_monthly_payout: float = 60000, next_stock_order: Optional[float] = None, return_rate_pct: float = 5,
                 daily_demand: float = 12, unit_cost: float = 210) -> dict:
    order = next_stock_order if next_stock_order is not None else js_round(daily_demand * 14 / 10) * 10 * unit_cost
    cap = 2 * avg_monthly_payout
    base = min(cap, order)
    lim = js_round(base * (1 - return_rate_pct / 100) / 1000) * 1000
    return {"limit": lim, "two_x_payout": cap, "next_stock_order": order, "base": base, "return_rate_pct": return_rate_pct,
            "formula": "min(2 × avg monthly payout, next stock order) × (1 − return rate)",
            "repayment": "Repaid directly to the NBFC through its own collection, never deducted by ProfitPilot.",
            "lender": "Via Meesho Finance NBFC partners; eligibility, rate and limit are decided by the regulated lender, with consent."}


def slab(weight_kg: float) -> int:
    for w, fee in SLABS:
        if weight_kg <= w:
            return fee
    return SLABS[-1][1]


def weight_audit(declared_kg: float, scanned_kg: float) -> dict:
    a, b = slab(declared_kg), slab(scanned_kg)
    return {"declared_slab_fee": a, "actual_slab_fee": b, "extra_per_order": b - a, "mismatch": b > a,
            "action": "Right-size packaging or update the declared weight." if b > a else "No action needed."}


def packaging_kit(category: str) -> dict:
    k = PACK_KITS[category]
    return {**k, "floor_saving": max(0, k["current"] - k["cost"]) + (8 if category == "decor" else 0)}
