"""Weekly recommendation per product and goal mode (the Yes / No card).

This is a line-by-line port of recFor() in the app, so the server and the app
always propose the same move. Every card carries a Why: what, why (signals with
numbers), ₹ effect, confidence and undo, plus the pre-flight checks.
"""
from __future__ import annotations

from typing import Optional

from .catalog import COOLDOWN_DAYS, MAX_MOVES_PER_MONTH, Product
from .demand import confidence_from_views, demand
from .floor import FloorResult, floor_for
from .fmt import ceil9, floor9, group_in, inr, js_round, pct, to_fixed
from .pricing import preflight


def recommend(p: Product, mode: str, f: Optional[FloorResult] = None) -> dict:
    f = f or floor_for(p)
    g = p.signals
    price = p.live_price
    F = f.F
    kA = js_round(f.k * 100)
    kr = g.kr if g.kr else kA
    r = {"id": p.id, "from": price, "to": price, "kind": "hold", "m": mode}
    cooldown = p.days_since_move < COOLDOWN_DAYS or p.moves_this_month >= MAX_MOVES_PER_MONTH

    def eff(to: int) -> dict:
        o1, o2 = demand(p, price), demand(p, to)
        d1, d2 = o1 * f.k * (price - F), o2 * f.k * (to - F)
        txt = (f"{inr(to - F)} profit per kept order (was {inr(price - F)}) · orders {to_fixed(o1, 1)} → {to_fixed(o2, 1)}/day"
               f" · profit/day {inr(d1)} → {inr(d2)} ({pct((d2 - d1) / abs(d1 or 1) * 100)})")
        return {"o1": o1, "o2": o2, "d1": d1, "d2": d2, "txt": txt}

    def growth_tree(hit: str) -> dict:
        return {"t": "Growth decision tree", "out": hit, "n": [
            {"q": "Conversion holds 2 weeks? (CVR ≥ median)", "no": "hold price, fix funnel (Diagnose)", "yes": "continue",
             "v": "yes" if (g.cvr >= g.cvr_med and g.cvr_weeks >= 2) else "no",
             "d": f"CVR {g.cvr:g}% vs median {g.cvr_med:g}% for {g.cvr_weeks} wk"},
            {"q": "Stock cover ≥ 30 days?", "no": "replenish first (reorder alert, 2.0)", "yes": "continue",
             "v": "yes" if g.doi >= 30 else "no", "d": f"{g.doi:g} days of stock"},
            {"q": "Kept rate on matured orders ≥ what the floor assumes?", "no": "fix returns first (Diagnose)", "yes": "continue",
             "v": "yes" if kr >= kA else "no", "d": f"{kr:g}% kept vs {kA}% assumed in F"},
            {"q": "Rival undercuts > 5%?", "yes": "compare profit/day: partial match (never below floor) only if it beats holding",
             "no": "step +3–5%; Meesho can widen reach to similar regions (seller approves)",
             "v": "yes" if g.rival > 5 else "no",
             "d": f"closest rival {g.rival:g}% lower" if g.rival > 0 else (f"rivals moved UP {-g.rival:g}%" if g.rival < 0 else "no undercut")}]}

    def decline_tree() -> dict:
        return {"t": "Decline decision tree", "out": "bundle → markdown", "n": [
            {"q": "Kept units −15% over 4 weeks, or days of inventory > 60?", "no": "stay mature: protect margin", "yes": "continue",
             "v": "yes" if (g.g < -15 or g.doi > 60) else "no", "d": f"g = {pct(g.g)}, DOI {g.doi:g} days"},
            {"q": "Seasonal dip?", "yes": "park for next season, or Meesho widens reach to similar regions (seller approves)",
             "no": "continue", "v": "no", "d": "demand fell outside the category's seasonal pattern (demo)"},
            {"q": "Price > full cost F?", "yes": "bundle first → markdown (≤ 8% steps, never below F without consent)",
             "no": "Exit only: B2B lot or delist, with seller consent", "v": "yes" if price > F else "no",
             "d": f"{inr(price)} vs F {inr(F)}"}]}

    def undo_move(to: int) -> str:
        return (f"Nothing changes until you tap YES. Undo in 24 h. Auto-revert check at 14 days: if profit per impression at "
                f"{inr(to)} is worse than at {inr(price)}, the price goes back to {inr(price)}.")

    base = [f"Floor F = {inr(F)} (return-adjusted, kept rate k = {f.k:.2f})",
            f"{group_in(g.views)} views at {inr(price)} (min 1,000)"]

    if cooldown:
        r.update(kind="hold", h=f"Hold {inr(price)}: cooldown",
                 sub=f"Last move {p.days_since_move} day(s) ago · {p.moves_this_month} move(s) this month.",
                 why=[f"Cooldown is 7 days per SKU and ≤ 2 moves a month; this SKU moved {p.days_since_move} day(s) ago "
                      f"({p.moves_this_month} this month).", *base],
                 eff="₹0 now. The 14-day auto-revert check on the last move is still running.",
                 undo="Nothing to undo. Next weekly check in 7 days.")
        return _fin(r, p, f)

    if mode == "clear":
        if price > f.clear_price:
            to = max(f.clear_price, ceil9(price * 0.92))
            e = eff(to)
            r.update(to=to, kind="down", h=f"Bundle first, then {inr(price)} → {inr(to)}",
                     sub=f"CLEAR mode: sell fast, never below your cost {inr(F)}.",
                     why=[f"Goal mode CLEAR: objective = kept orders per impression, never below F {inr(F)}.",
                          f"Step capped at 8%: {inr(price)} × 0.92 = {inr(price * 0.92)} → {inr(to)} ({pct((to - price) / price * 100, 1)}).",
                          f"Clearance target {inr(f.clear_price)} = F ÷ 0.97 (m = 3%). Deeper recovery floor {inr(f.recovery_floor)} "
                          f"only at Exit with explicit consent.", *base],
                     eff=e["txt"], undo=undo_move(to),
                     logic={"t": "CLEAR mode guard", "out": "bundle + step", "n": [
                         {"q": "Bundle with a fast mover possible?", "yes": "offer bundle first (recovers 50–80%)", "no": "continue",
                          "v": "yes", "d": "demo: yes"},
                         {"q": "Next step ≥ floor F?", "yes": "markdown ≤ 8% step", "no": "stop at F; recovery floor needs consent at Exit",
                          "v": "yes" if to >= F else "no", "d": f"{inr(to)} vs {inr(F)}"}]})
            return _fin(r, p, f)
        r.update(h=f"Hold {inr(price)}: already at clearance floor",
                 why=[f"Price {inr(price)} is at F ÷ 0.97 = {inr(f.clear_price)}. CLEAR never goes below F {inr(F)} without Exit-stage consent."],
                 eff="₹0", undo="Nothing changes.")
        return _fin(r, p, f)

    if mode == "cash" and g.stage != "decline":
        cd_e, cd_n = 15, 8
        rpd_e = (f.start_price - F) / (F * cd_e)
        pn = price - f.gap
        rpd_n = (pn - f.floor_no_return) / (f.floor_no_return * cd_n)
        r.update(to=pn, kind="dual", h=f"Lead with No-return {inr(pn)} + prepaid nudge",
                 sub=f"Easy-returns {inr(price)} stays on. No-return orders can't come back, so cash is final sooner.",
                 why=["Goal mode CASH: objective = ₹ profit per rupee-day.",
                      f"No-return floor = F − C_ret = {inr(F)} − {inr(f.c_ret)} = {inr(f.floor_no_return)}; profit per kept order "
                      f"{inr(pn - f.floor_no_return)} (vs {inr(price - F)} easy-returns).",
                      f"Assumed cash cycle: easy-returns ≈ {cd_e} days (return window), no-return ≈ {cd_n} days (assumption to confirm with Meesho).",
                      "Meesho dual pricing: >30% of buyers pick the lower no-return price; ~10% fewer returns (source: Upraised)."],
                 eff=f"Profit per rupee-day: {to_fixed(rpd_e * 100, 2)}% → {to_fixed(rpd_n * 100, 2)}% ({to_fixed(rpd_n / rpd_e, 1)}×). "
                     f"Price shown to buyers who pick no-return: {inr(pn)}.",
                 undo="Uses Meesho's existing dual-pricing field; switch the lead back to easy-returns any time. 14-day check on cash cycle and returns.",
                 logic={"t": "CASH mode routing", "out": "dual price, no-return first", "n": [
                     {"q": "Is the no-return price ≥ its own floor (F − C_ret)?", "yes": "lead with no-return + prepaid nudge",
                      "no": "keep easy-returns", "v": "yes" if pn >= f.floor_no_return else "no",
                      "d": f"{inr(pn)} vs {inr(f.floor_no_return)}"}]})
        return _fin(r, p, f)

    if mode == "margin":
        tgt = f.margin_price
        if g.stage == "decline":
            r.update(kind="hold", h=f"Hold {inr(price)} · bundle instead of markdown", sub="MARGIN mode accepts lower volume; no markdown.",
                     why=[f"Goal mode MARGIN: only prices with ≥ {inr(f.T)} profit per kept order.",
                          f"Stock is stuck (DOI {g.doi:g} days), but a markdown would break your margin rule. Bundle keeps the unit price.", *base],
                     eff=f"Keeps {inr(price - F)} per kept order; stock clears slower (~{js_round(g.doi * 0.8)} days with a bundle).",
                     undo="Nothing changes on price.", logic=decline_tree())
            return _fin(r, p, f)
        if price < tgt:
            to = min(tgt, floor9(price * 1.08))
            if to <= price:
                to = js_round(price * 1.08)
            e = eff(to)
            r.update(to=to, kind="up", h=f"Raise {inr(price)} → {inr(to)} (margin target)",
                     sub=f"MARGIN mode: target {inr(tgt)} (m = 22%), capped at 8% per step.",
                     why=[f"Goal mode MARGIN: target P* = F ÷ (1 − m) = {inr(F)} ÷ 0.78 = {inr(F / 0.78)} → {inr(tgt)}.",
                          f"Only arms with ≥ {inr(f.T)} profit per kept order are allowed ({inr(f.start_price)}–{inr(f.price_points[4])}).",
                          f"Max step 8%: {inr(price)} × 1.08 = {inr(price * 1.08)} → {inr(to)}. Lower volume is accepted in this mode.", *base],
                     eff=e["txt"], undo=undo_move(to))
            return _fin(r, p, f)
        r.update(h=f"Hold {inr(price)}: margin already met",
                 why=[f"Profit per kept order {inr(price - F)} ≥ target {inr(f.T)}; m = {to_fixed((1 - F / price) * 100, 0)}%.", *base],
                 eff="₹0 · no change", undo="Nothing changes. Next weekly check in 7 days.")
        return _fin(r, p, f)

    # growth (default) + cash-in-decline
    if g.stage == "growth":
        A = g.cvr >= g.cvr_med and g.cvr_weeks >= 2
        B = g.doi >= 30
        C = g.rival > 5
        K = kr >= kA
        if A and B and K and not C:
            to = js_round(price * 1.04)
            e = eff(to)
            r.update(to=to, kind="up", h=f"Step up {inr(price)} → {inr(to)} (+4%)", sub="Demand is growing and conversion held 2 weeks.",
                     why=[f"Stage GROWTH: kept units {pct(g.g)} in 4 weeks (Growth line +15%).",
                          f"Conversion {g.cvr:g}% vs median {g.cvr_med:g}% for {g.cvr_weeks} weeks → step +3–5%.",
                          f"Kept rate on matured orders {kr:g}% ≥ {kA}% assumed in the floor.",
                          f"Stock cover {g.doi:g} days (≥ 30). No rival undercut > 5%.",
                          f"{inr(price)} × 1.04 = {inr(to)}: inside the 8% limit and above floor {inr(F)}.", *base],
                     eff=e["txt"], undo=undo_move(to), logic=growth_tree("step +4%"))
            return _fin(r, p, f)
        reason = (f"Conversion has not held 2 weeks ({g.cvr:g}% vs {g.cvr_med:g}%)." if not A else
                  f"Only {g.doi:g} days of stock: replenish first." if not B else
                  f"Kept rate {kr:g}% is below the {kA}% the floor assumes: fix returns first." if not K else
                  f"Rival undercut {g.rival:g}%: see Lifecycle for the match test.")
        r.update(h=f"Hold {inr(price)}", why=[reason, *base], eff="₹0", undo="Nothing changes.", logic=growth_tree("hold"))
        return _fin(r, p, f)

    if g.stage == "maturity":
        if g.rival < 0:
            to = js_round(price * 1.04)
            e = eff(to)
            r.update(to=to, kind="up", h=f"Raise {inr(price)} → {inr(to)}: rivals moved up",
                     sub=f"4 of 6 closest rivals moved to {inr(p.band[1] - 20)}–{inr(p.band[1])}. You stay below them.",
                     why=[f"Stage MATURITY: demand steady ({pct(g.g)}).",
                          f"Closest rivals moved UP {-g.rival:g}% this week; market median now {inr(p.median)}.",
                          f"{inr(price)} × 1.04 = {inr(to)} (+4%, limit 8%), {inr(to - F)} above floor {inr(F)}.", *base],
                     eff=e["txt"], undo=undo_move(to))
            return _fin(r, p, f)
        r.update(h=f"Hold {inr(price)}: no trigger crossed",
                 sub=(f"Stock is low ({g.doi:g} days): reorder before any price move." if g.doi < 30
                      else "Steady demand. Margin is protected, not chased."),
                 why=[f"Stage MATURITY: demand {pct(g.g)} (inside ±15%).",
                      (f"Stock cover {g.doi:g} days is below 30: a price step now would sell out faster. "
                       f"Reorder point ROP = 84 + 18 = 102 units (2.0 reorder alert).") if g.doi < 30 else f"Stock {g.doi:g} days, healthy.",
                      "No rival undercut > 5% for 7 days.", *base],
                 eff="₹0 · holding avoids resetting learning and uses none of your 2 moves/month.",
                 undo="Nothing changes. Next weekly check in 7 days.")
        return _fin(r, p, f)

    if g.stage == "decline":
        to = max(f.clear_price, ceil9(price * 0.92))
        e = eff(to)
        r.update(to=to, kind="down", h=f"Bundle first, then {inr(price)} → {inr(to)}",
                 sub=f"{g.doi:g} days of stock. Price is not the main problem; free the cash safely.",
                 why=[f"Stage DECLINE: kept units {pct(g.g)} in 4 weeks (line −15%); days of inventory {g.doi:g} (limit 60).",
                      "Bundle with a fast mover recovers 50–80% of cost vs markdown 40–70%.",
                      f"Markdown step {pct((to - price) / price * 100, 1)} (≤ 8%), stays ≥ F {inr(F)}. Below F only at Exit with consent, "
                      f"never below {inr(f.recovery_floor)}.", *base],
                 eff=e["txt"] + f" · stock clears in ≈ {js_round(g.doi * e['o1'] / e['o2'])} days",
                 undo=undo_move(to), logic=decline_tree())
        return _fin(r, p, f)

    r.update(h="Launch: dual-price menu + sanity check",
             why=["Launch: the buyer picks easy-returns or no-return. Days 1–14 check that buyers order and returns are normal; "
                  "price tests rotate by day from day 15."], eff="—", undo="—")
    return _fin(r, p, f)


def _fin(r: dict, p: Product, f: FloorResult) -> dict:
    v = p.signals.views
    r["conf"] = confidence_from_views(v)
    band = "≥ 5,000" if r["conf"] == "High" else ("1,000–5,000" if r["conf"] == "Medium" else "< 1,000")
    r["confWhy"] = (f"{group_in(v)} views; {band} → {r['conf'].lower()}. Demand: day 0 blends category and look-alikes "
                    f"(β = −3 to start), then moves toward this product's own estimate once it has sold at two or more prices (w = n/(n+n₀)).")
    r.setdefault("sub", None)
    r.setdefault("logic", None)
    r["what"] = r["h"] + (". " + r["sub"] if r.get("sub") else "")
    r["key"] = f"{p.id}:{r['m']}:{r['from']}>{r['to']}:{r['kind']}"
    r["pf"] = preflight(p, r["from"], r["to"], f) if (r["to"] != r["from"] and r["kind"] != "dual") else None
    return r
