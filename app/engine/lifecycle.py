"""Lifecycle: stage classifier, the rival test (hold vs match), markdown ladder,
decline exits, and the simulated life story the app's time machine shows."""
from __future__ import annotations

from typing import List, Optional, Sequence

from .catalog import CATEGORIES, Product
from .demand import demand
from .floor import FloorResult, floor_for
from .fmt import ceil9, inr, js_round, pct, to_fixed

RIVAL_SHARE_TAKEN = 0.30     # a rival undercutting > 5% takes ~30% of orders at the held price


# ---------------------------------------------------------------- stage classifier
def classify_stage(age_days: int, kept_units_weekly: Sequence[float], stock_units: Optional[float] = None,
                   avg_daily_units: Optional[float] = None, stock_age_days: Optional[float] = None,
                   season_over: bool = False, last_season_same_weeks: Optional[Sequence[float]] = None) -> dict:
    """Data-defined stage.

    g   = kept units in the last 4 weeks ÷ kept units in the 4 weeks before − 1
          (seasonal products: ÷ the same 4 weeks last season)
    DOI = stock ÷ average daily units
    Launch: age < 30 days · Growth: g > +15% · Maturity: |g| ≤ 15%
    Decline: g < −15% or DOI > 60 · Exit: stock older than 90 days or season over.
    """
    w = list(kept_units_weekly)
    g = None
    if last_season_same_weeks and len(w) >= 4 and sum(last_season_same_weeks[-4:]) > 0:
        g = (sum(w[-4:]) / sum(last_season_same_weeks[-4:]) - 1) * 100
    elif len(w) >= 8 and sum(w[-8:-4]) > 0:
        g = (sum(w[-4:]) / sum(w[-8:-4]) - 1) * 100
    doi = None
    if stock_units is not None and avg_daily_units:
        doi = stock_units / avg_daily_units
    if age_days < 30:
        stage = "launch"
    elif (stock_age_days is not None and stock_age_days > 90) or season_over:
        stage = "exit"
    elif (g is not None and g < -15) or (doi is not None and doi > 60):
        stage = "decline"
    elif g is not None and g > 15:
        stage = "growth"
    else:
        stage = "maturity"
    return {"stage": stage, "g": None if g is None else round(g, 1), "doi": None if doi is None else round(doi, 1),
            "rules": "Launch: age < 30 d · Growth: g > +15% · Maturity: |g| ≤ 15% · Decline: g < −15% or DOI > 60 · Exit: stock age > 90 d / season end"}


# ---------------------------------------------------------------- rival test
def rival_test(p: Product, held_price: int, base_price: int, f: Optional[FloorResult] = None,
               rival_price: Optional[int] = None) -> dict:
    """Should we follow a rival who undercuts us by more than 5%?

    Hold: the rival takes ~30% of our orders at the held price.
    Match: the rival is STILL there, so demand starts from the post-rival level
    and only gains the price effect (β = −3). Match only if it earns more per day.
    """
    f = f or floor_for(p)
    pre = js_round(demand(p, held_price))
    hold_o = js_round(demand(p, held_price) * (1 - RIVAL_SHARE_TAKEN))
    match_o = float(to_fixed(hold_o * (held_price / base_price) ** 3, 1))
    hold = hold_o * f.k * (held_price - f.F)
    match = match_o * f.k * (base_price - f.F)
    do_match = match > hold
    rv = rival_price if rival_price is not None else js_round(held_price * 0.935)
    win = (match / hold - 1) * 100 if do_match else (hold / match - 1) * 100
    return {
        "held_price": held_price, "match_price": base_price, "rival_price": rv,
        "orders_before_rival": pre, "orders_if_hold": hold_o, "orders_if_match": match_o,
        "profit_day_hold": round(hold, 1), "profit_day_match": round(match, 1),
        "decision": "match" if do_match else "hold", "winner_margin_pct": round(win, 1),
        "why": [
            f"Rival at {inr(rv)} ({pct((rv - held_price) / held_price * 100, 1)}; trigger > 5%).",
            f"Hold {inr(held_price)}: the rival takes ~30% of orders, {pre} → {hold_o}/day → {hold_o} × {f.k:.2f} × {inr(held_price - f.F)} = {inr(hold)}/day.",
            f"Match {inr(base_price)}: the rival is still there, so start from {hold_o}/day and apply β = −3: "
            f"{hold_o} × ({held_price}/{base_price})³ = {match_o}/day → {match_o} × {f.k:.2f} × {inr(base_price - f.F)} = {inr(match)}/day.",
            f"Rule: match only if profit/day after the match beats profit/day if held; never below F {inr(f.F)}; never copy {inr(rv)}.",
        ],
    }


# ---------------------------------------------------------------- markdown ladder and exits
def markdown_ladder(start: int, f: FloorResult) -> List[int]:
    m1 = ceil9(start * 0.92)
    m2 = ceil9(m1 * 0.92)
    m3 = max(f.clear_price, ceil9(m2 * 0.92))
    return [m1, m2, m3]


EXITS = [  # key, label, deck range, default sell-through %, net cash factor × F, freight per unit
    ("bundle", "Bundle with a fast mover", "50–80%", 85, 0.78, 0),
    ("md", "Markdown ladder", "40–70%", 75, 0.73, 0),
    ("reg", "Wider reach: similar regions (Meesho, seller approves)", "+30–60% local sell-through", 45, 0.85, 15),
    ("b2b", "B2B / bulk lot", "20–50%", 100, 0.35, 0),
]


def exit_options(f: FloorResult, overrides: Optional[dict] = None) -> List[dict]:
    """Value recovered per stuck unit: sell-through × net cash per unit ÷ F."""
    out = []
    for key, label, rng, st_default, factor, freight in EXITS:
        v = (overrides or {}).get(key, st_default)
        st = min(100, 45 * (1 + v / 100)) if key == "reg" else v
        net = factor * f.F - freight
        rec = st / 100 * net / f.F * 100
        out.append({"key": key, "label": label, "deck_range": rng, "sell_through_pct": st, "net_per_unit": round(net, 1),
                    "recovered_pct_of_cost": round(rec, 1)})
    out.append({"key": "return", "label": "Return / donate", "deck_range": "−₹40–60/unit", "note": "logistics only"})
    out.append({"key": "park", "label": "Park for next season", "holding_cost_per_month": round(f.F * 0.02, 1)})
    return out


# ---------------------------------------------------------------- simulated life story (time machine)
def life_story(p: Product, P0o: Optional[int] = None) -> dict:
    """Port of lcModel(): a simulated life from launch to exit, with the rival test decided by the numbers."""
    f = floor_for(p)
    L = p.life_days
    sc = L / 180
    P0 = P0o or p.ref_price
    P1 = js_round(P0 * 1.04)
    Rv = js_round(P1 * 0.935)

    def D(x: float) -> int:
        return js_round(x * sc)

    w = {"launch": D(30), "growth": D(60), "maturity": D(120), "decline": D(155)}
    rt = rival_test(p, P1, P0, f, Rv)
    do_match = rt["decision"] == "match"
    Pm = P0 if do_match else P1
    # products already past the rival event: their live price is the post-rival price
    if not P0o and not do_match and p.signals.day > D(56):
        return life_story(p, js_round(p.ref_price / 1.04))
    M1, M2, M3 = markdown_ladder(Pm, f)
    ev = [[D(38), P1, f"{inr(P0)} → {inr(P1)} (+4% Growth step)"],
          [D(52), P1, f"Rival lists {inr(Rv)}"],
          [D(56), Pm, f"Partial match {inr(P1)} → {inr(P0)}" if do_match else f"Hold {inr(P1)}: matching earns less"],
          [D(140), M1, f"Markdown {inr(Pm)} → {inr(M1)}"],
          [D(162), M2, f"{inr(M1)} → {inr(M2)}"],
          [D(176), M3, f"{inr(M2)} → {inr(M3)} (clearance, ≥ F)"]]
    a, b = demand(p, P0), demand(p, P1)
    c = rt["orders_if_match"] if do_match else rt["orders_if_hold"]
    e = demand(p, Pm)
    shape = [[0, a * 8 / 23], [15, a * 14 / 23], [30, a * 19 / 23], [36, a], [38, b], [52, b], [54, rt["orders_if_hold"]],
             [56, c], [90, e], [105, e * 21 / 22], [120, e * 17 / 22], [130, e * 11 / 22], [140, e * 7 / 22],
             [155, e * 5 / 22], [175, e * 4 / 22], [180, e * 3 / 22]]
    orders = [[D(x), float(to_fixed(y, 1))] for x, y in shape]
    return {"P0": P0, "P1": P1, "rival": Rv, "post_rival_price": Pm, "markdowns": [M1, M2, M3],
            "windows": w, "events": ev, "orders_per_day": orders, "rival_test": rt,
            "floor": f.F, "recovery_floor": f.recovery_floor, "category_life": CATEGORIES[p.category]["life"]}


def stage_on_day(story: dict, d: int) -> str:
    w = story["windows"]
    return ("launch" if d < w["launch"] else "growth" if d < w["growth"] else "maturity" if d < w["maturity"]
            else "decline" if d < w["decline"] else "exit")


def price_on_day(story: dict, d: int) -> int:
    price = story["P0"]
    for day, pr, _ in story["events"]:
        if d >= day:
            price = pr
    return price
