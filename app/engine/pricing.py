"""First price, goal-mode prices, the price simulator and the pre-flight guardrails."""
from __future__ import annotations

from typing import List, Optional

from .catalog import (CATEGORIES, COOLDOWN_DAYS, MAX_MOVES_PER_MONTH, MAX_STEP_PCT, MIN_VIEWS, MODES, Product)
from .demand import demand
from .floor import FloorResult, floor_for
from .fmt import floor9, group_in, inr, js_round, pct


# ---------------------------------------------------------------- guardrails
def preflight(p: Product, frm: int, to: int, f: Optional[FloorResult] = None, views: Optional[float] = None,
              dsm: Optional[int] = None, mpm: Optional[int] = None, consent: bool = False) -> List[dict]:
    """Checks every price move must pass before a card is shown or a price is published."""
    f = f or floor_for(p)
    step = (to - frm) / frm * 100
    v = p.signals.views if views is None else views
    d = p.days_since_move if dsm is None else dsm
    mm = p.moves_this_month if mpm is None else mpm
    floor_ok = to >= f.recovery_floor if consent else to >= f.F
    return [
        {"k": "Floor", "ok": floor_ok,
         "d": f"{inr(to)} ≥ recovery floor {inr(f.recovery_floor)} (seller consented)" if consent
         else f"{inr(to)} {'≥' if to >= f.F else '<'} floor {inr(f.F)}"},
        {"k": "Step ≤ 8%", "ok": abs(step) <= MAX_STEP_PCT + 0.0001, "d": f"{pct(step, 1)} ({inr(frm)} → {inr(to)})"},
        {"k": "Views ≥ 1,000", "ok": v >= MIN_VIEWS, "d": f"{group_in(js_round(v))} views at current price (sanity check)"},
        {"k": "Cooldown 7 days", "ok": d >= COOLDOWN_DAYS, "d": f"{d} day{'' if d == 1 else 's'} since last move"},
        {"k": "≤ 2 moves / month", "ok": mm < MAX_MOVES_PER_MONTH, "d": f"{mm} move{'' if mm == 1 else 's'} this month"},
        {"k": "Auto-revert at 14 days", "ok": True, "na": True,
         "d": "scheduled: day 14 on orders, day 28 confirmed on kept orders"},
    ]


def preflight_ok(checks: List[dict]) -> bool:
    return all(c["ok"] for c in checks)


# ---------------------------------------------------------------- goal modes
def mode_price(f: FloorResult, mode: str) -> dict:
    if mode == "cash":
        return {"lead": f.no_return_price, "alt": f.start_price,
                "label": f"No-return {inr(f.no_return_price)} + prepaid nudge", "m": 1 - f.floor_no_return / f.no_return_price}
    if mode == "growth":
        return {"lead": f.no_return_price, "alt": f.start_price,
                "label": f"{inr(f.no_return_price)}–{inr(f.start_price)}", "m": 1 - f.F / f.start_price}
    if mode == "margin":
        pts = f.price_points
        return {"lead": f.margin_price, "alt": f.start_price + 2 * (pts[3] - f.start_price),
                "label": f"{inr(f.margin_price)} (arms {inr(f.start_price)}–{inr(pts[4])})", "m": 1 - f.F / f.margin_price}
    return {"lead": f.clear_price, "alt": f.clear_price,
            "label": f"{inr(f.clear_price)} · never below {inr(f.F)}", "m": 1 - f.F / f.clear_price}


# ---------------------------------------------------------------- first price
def first_price(p: Product, f: Optional[FloorResult] = None) -> dict:
    """The launch recommendation for a product with no sales history."""
    f = f or floor_for(p)
    lo, hi = p.band
    v9 = floor9(p.median - 1)          # largest price ending in 9 below the look-alike median
    if f.start_price < lo:
        position = "below band"
    elif f.start_price > hi:
        position = "above band"
    else:
        position = "competitive"
    warning = None
    if f.start_price > hi:
        warning = {"title": f"Floor + your profit {inr(f.start_price)} is above the market band {inr(lo)}–{inr(hi)}.",
                   "levers": ["lighter pack", "bundle", "variant", "lower target T", "skip this product"],
                   "message": "Don't launch at a loss. Try a lighter pack, a bundle or a variant, lower your target T, or skip this product."}
    why = [
        f"Start price = floor F + your profit T = {inr(f.F)} + {inr(f.T)} = {inr(f.start_price)}. "
        f"That is {inr(f.start_price - f.safe_minimum)} above floor + {inr(f.safety_margin)} safety margin ({inr(f.safe_minimum)}).",
        f"{'Inside' if position == 'competitive' else 'Outside'} the look-alike band {inr(lo)}–{inr(hi)}; "
        f"{'just under' if f.start_price < p.median else 'above'} the median {inr(p.median)}.",
        f"Velocity check: {p.lookalikes} look-alikes (crowded = 10+). Largest price ending in 9 below the median: {inr(v9)}. "
        f"{inr(f.start_price)} {'sits at or under it' if f.start_price <= v9 else 'is above it'} and covers floor + your profit.",
        "New listings get extra reach in the first ~14 days (assumed), so early conversion matters.",
        "Floor + your profit is the start; the engine then fine-tunes inside the look-alike band.",
    ]
    kept_day = demand(p, f.start_price) * f.k
    return {
        "product_id": p.id,
        "floor": f.to_dict(),
        "start_price": f.start_price,
        "no_return_price": f.no_return_price,
        "gap": f.gap,
        "safe_minimum": f.safe_minimum,
        "above_safe_minimum_by": f.start_price - f.safe_minimum,
        "market": {"band": [lo, hi], "median": p.median, "lookalikes": p.lookalikes, "position": position,
                   "velocity_price": v9, "velocity_ok": f.start_price <= v9, "rival_price": p.rival_price,
                   "rival_loss_per_kept_order": f.F - p.rival_price},
        "warning": warning,
        "modes": {m: mode_price(f, m) for m in MODES},
        "dual_price": {"easy_returns": f.start_price, "no_return": f.no_return_price,
                       "profit_easy": f.start_price - f.F, "profit_no_return": f.no_return_price - f.floor_no_return,
                       "floor_easy": f.F, "floor_no_return": f.floor_no_return},
        "expected": {"kept_per_day": round(kept_day, 1), "profit_per_day": js_round(f.T * kept_day),
                     "note": "orders/day at full speed; new listings start slower"},
        "launch_plan": ["Days 1–14: dual-price menu, sanity check only (are buyers ordering? are returns normal?).",
                        "From day 15: price menus rotate by day, one menu for every buyer at any moment.",
                        "Price sensitivity is learned across similar products (pooled), then from this listing's own tests."],
        "why": why,
        "category": {"code": p.category, "name": CATEGORIES[p.category]["name"], "life": CATEGORIES[p.category]["life"]},
    }


# ---------------------------------------------------------------- simulator
def simulate(p: Product, price: int, kink: bool = True, f: Optional[FloorResult] = None) -> dict:
    f = f or floor_for(p)
    o = demand(p, price, kink)
    kept = o * f.k
    per_kept = price - f.F
    lo, hi = p.band
    zone = "loss" if price < f.F else ("thin" if price < lo else ("competitive" if price <= hi else "high"))
    pn = price - f.gap
    return {
        "price": price, "orders_per_day": round(o, 2), "kept_per_day": round(kept, 2),
        "profit_per_kept_order": per_kept, "profit_per_day": round(kept * per_kept, 1),
        "zone": zone, "loss_warning": price < f.F,
        "loss_per_day": round((f.F - price) * kept, 1) if price < f.F else 0,
        "dual": {"easy_returns": price, "easy_profit": per_kept, "no_return": pn, "no_return_profit": pn - f.floor_no_return},
    }


def profit_curve(p: Product, lo: Optional[int] = None, hi: Optional[int] = None, step: int = 2, kink: bool = True) -> dict:
    f = floor_for(p)
    lo = lo if lo is not None else min(f.price_points[0], p.rival_price, f.F - 10)
    hi = hi if hi is not None else js_round(f.start_price * 1.217)
    pts = [{"price": x, "profit_per_day": round(demand(p, x, kink) * f.k * (x - f.F), 1)} for x in range(lo, hi + 1, step)]
    best = max(range(lo, hi + 1), key=lambda x: demand(p, x, kink) * f.k * (x - f.F))
    return {"floor": f.F, "points": pts, "peak_price": best}
