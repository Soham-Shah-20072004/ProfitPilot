"""Engine tools the AI Coach can call (Gemini function calling).

Every tool is read-only: it computes with the ProfitPilot engine and returns
numbers plus a `source` label. Nothing here changes a price, stock order or
mode. `propose_action` only returns a card; the seller's tap on Yes (POST
/decisions) is the only thing that changes anything, and the server re-checks
the guardrails then.
"""
from __future__ import annotations

import ast
import operator
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..engine import inventory
from ..engine.catalog import CATEGORIES, MAX_STEP_PCT, MODES, Product as EProduct
from ..engine.demand import confidence_from_views, demand
from ..engine.diagnose import DiagnoseInput, Peers, Week, diagnose
from ..engine.floor import floor_for
from ..engine.fmt import js_round
from ..engine.lifecycle import exit_options, life_story
from ..engine.pricing import first_price, mode_price, profit_curve, simulate
from ..engine.recommend import recommend
from ..models import MetricWeek, Product, Seller
from ..services.core import now, to_engine


@dataclass
class ToolContext:
    db: Session
    seller: Seller
    products: Dict[str, EProduct]
    default_pid: str
    mode: str
    t: datetime

    @classmethod
    def build(cls, db: Session, seller: Seller, product_id: Optional[str] = None, mode: Optional[str] = None) -> "ToolContext":
        t = now(db)
        rows = db.scalars(select(Product).where(Product.seller_id == seller.id)).all()
        prods = {r.id: to_engine(db, r, t) for r in rows}
        pid = product_id if product_id in prods else (next(iter(prods)) if prods else "")
        return cls(db, seller, prods, pid, mode or seller.goal_mode, t)

    def product(self, pid: Optional[str]) -> EProduct:
        pid = pid or self.default_pid
        if pid not in self.products:
            # accept names too: "kurti", "Printed Kurti", "lunch box"
            low = (pid or "").lower()
            for k, p in self.products.items():
                if low and (low in p.name.lower() or p.name.lower() in low or low in k):
                    return p
            raise ToolError(f"unknown product {pid!r}; call list_products")
        return self.products[pid]


class ToolError(ValueError):
    pass


# ---------------------------------------------------------------- tool bodies
def _floor_dict(p: EProduct) -> dict:
    f = floor_for(p)
    return {"F": f.F, "kept_rate_k": f.k, "parts": {"sourcing": f.cs, "packaging": f.pack, "forward_shipping": f.fwd,
            "return_cost_per_kept": f.c_ret, "rto_cost_per_kept": f.c_rto, "other": f.other, "other_parts": f.other_parts},
            "B_return_plus_rto": f.B, "safety_margin": f.safety_margin, "safe_minimum": f.safe_minimum,
            "per_100_placed": {"dispatched": 97, "delivered": f.delivered, "returned": f.returns_n, "rto": f.rto_n, "kept": f.kept},
            "no_return_floor": f.floor_no_return, "dual_price_gap": f.gap, "recovery_floor_exit_only": f.recovery_floor}


def t_list_products(ctx: ToolContext) -> dict:
    out = []
    for k, p in ctx.products.items():
        f = floor_for(p)
        out.append({"id": k, "name": p.name, "category": CATEGORIES[p.category]["name"], "live_price": p.live_price,
                    "no_return_price": p.live_price - f.gap, "floor_F": f.F, "profit_per_kept_order": p.live_price - f.F,
                    "stage": p.signals.stage, "days_of_stock": p.signals.doi, "control": p.control})
    return {"products": out, "goal_mode": ctx.mode, "selected_product": ctx.default_pid, "source": "Seller catalogue · floor engine"}


def t_product_overview(ctx: ToolContext, product_id: Optional[str] = None) -> dict:
    p = ctx.product(product_id)
    f = floor_for(p)
    o = demand(p, p.live_price)
    g = p.signals
    return {"id": p.id, "name": p.name, "category": CATEGORIES[p.category]["name"], "live_price": p.live_price,
            "no_return_price": p.live_price - f.gap, "offline_price": p.offline_price, "floor_F": f.F,
            "profit_per_kept_order": p.live_price - f.F, "naive_profit_per_piece": p.live_price - p.cs,
            "orders_per_day_est": round(o, 1), "kept_per_day_est": round(o * f.k, 1),
            "profit_per_day_est": js_round(o * f.k * (p.live_price - f.F)), "target_profit_T": p.target,
            "return_rate_pct": p.ret, "rto_rate_pct": p.rto, "stage": g.stage, "days_since_launch": g.day,
            "views_at_price": g.views, "ctr_pct": g.ctr, "conversion_pct": g.cvr, "conversion_median_pct": g.cvr_med,
            "kept_unit_trend_4w_pct": g.g, "days_of_stock": g.doi, "rival_undercut_pct": g.rival,
            "days_since_last_move": p.days_since_move, "moves_this_month": p.moves_this_month,
            "confidence": confidence_from_views(g.views), "goal_mode": ctx.mode,
            "source": f"Floor engine + demand model (β {p.beta:g}) · {p.name}"}


def t_floor_breakdown(ctx: ToolContext, product_id: Optional[str] = None) -> dict:
    p = ctx.product(product_id)
    return {"product": p.name, **_floor_dict(p), "formula": "F = all costs of 100 placed orders ÷ kept orders",
            "source": "Floor engine (return-adjusted)"}


def t_recommendation(ctx: ToolContext, product_id: Optional[str] = None, mode: Optional[str] = None) -> dict:
    p = ctx.product(product_id)
    m = mode if mode in MODES else ctx.mode
    r = recommend(p, m)
    return {"product": p.name, "mode": m, "kind": r["kind"], "from": r["from"], "to": r["to"], "headline": r["h"],
            "what": r["what"], "why": r.get("why") or [], "effect": r.get("eff"), "confidence": r["conf"],
            "confidence_why": r["confWhy"], "undo": r.get("undo"), "card_key": r["key"],
            "preflight_checks": r.get("pf"), "source": f"Lifecycle engine ({p.signals.stage}) · {MODES[m]['name']} mode · bounded step ≤ {MAX_STEP_PCT:g}%"}


def t_simulate_price(ctx: ToolContext, price: float, product_id: Optional[str] = None) -> dict:
    p = ctx.product(product_id)
    price = int(round(float(price)))
    if price <= 0 or price > 100000:
        raise ToolError("price must be between ₹1 and ₹1,00,000")
    s = simulate(p, price)
    cur = simulate(p, p.live_price)
    s["current"] = {"price": p.live_price, "profit_per_day": cur["profit_per_day"], "orders_per_day": cur["orders_per_day"]}
    s["floor_F"] = floor_for(p).F
    s["band"] = p.band
    s["note"] = "Estimate from the demand model; orders are expected, not guaranteed."
    s["source"] = f"Simulator · demand model β {p.beta:g} · {p.name}"
    return s


def t_best_price(ctx: ToolContext, product_id: Optional[str] = None) -> dict:
    p = ctx.product(product_id)
    c = profit_curve(p)
    peak = c["peak_price"]
    return {"product": p.name, "profit_peak_price": peak, "profit_per_day_at_peak": simulate(p, peak)["profit_per_day"],
            "live_price": p.live_price, "profit_per_day_now": simulate(p, p.live_price)["profit_per_day"], "floor_F": c["floor"],
            "rule": f"The engine moves toward the peak only in ≤ {MAX_STEP_PCT:g}% steps, 7 days apart, max 2 a month.",
            "source": "Profit curve · demand model"}


def t_competition(ctx: ToolContext, product_id: Optional[str] = None) -> dict:
    p = ctx.product(product_id)
    f = floor_for(p)
    return {"product": p.name, "lookalikes": p.lookalikes, "band_p25_p75": p.band, "median": p.median,
            "cheapest_close_rival": p.rival_price, "loss_per_kept_order_at_rival_price": f.F - p.rival_price,
            "your_price": p.live_price, "floor_F": f.F,
            "policy": "No seller names; prices are never coordinated; your floor is your own.",
            "source": f"Live catalogue · {p.lookalikes} look-alikes"}


_REASON_SPLIT = {"ethnic": {"size_fit": 45, "not_as_shown": 25, "quality": 20, "damaged": 5, "other": 5},
                 "kids": {"size_fit": 50, "quality": 20, "not_as_shown": 20, "other": 10},
                 "kitchen": {"damaged": 40, "quality": 30, "not_as_shown": 20, "other": 10},
                 "beauty": {"damaged": 35, "quality": 30, "not_as_shown": 20, "other": 15},
                 "decor": {"damaged": 60, "not_as_shown": 25, "quality": 10, "other": 5}}


def t_returns_rto(ctx: ToolContext, product_id: Optional[str] = None) -> dict:
    p = ctx.product(product_id)
    f = floor_for(p)
    cat = CATEGORIES[p.category]
    return {"product": p.name, "return_rate_pct": p.ret, "rto_rate_pct": p.rto, "category_return_pct": cat["ret"],
            "category_rto_pct": cat["rto"], "returned_per_100_placed": f.returns_n, "rto_per_100_placed": f.rto_n,
            "return_cost_per_kept_order": f.c_ret, "rto_cost_per_kept_order": f.c_rto, "B": f.B,
            "return_reason_split_pct_illustrative": _REASON_SPLIT.get(p.category, {}),
            "seller_fixes": ["size chart", "true photos", "quality check before dispatch", "right packaging"],
            "meesho_side_fixes": ["COD confirmation call / WhatsApp", "prepaid nudge", "delivery attempt quality"],
            "dual_price": {"easy_returns": p.live_price, "no_return": p.live_price - f.gap, "gap": f.gap,
                           "why_gap": f"No-return orders do not carry the ₹{f.c_ret} return cost."},
            "source": f"Returns & RTO · {p.name} · category prior"}


def _diag_input(ctx: ToolContext, p: EProduct) -> DiagnoseInput:
    rows = ctx.db.scalars(select(MetricWeek).where(MetricWeek.product_id == p.id).order_by(MetricWeek.week_start)).all()
    cat = CATEGORIES[p.category]
    g = p.signals
    peers = Peers(impressions_median=4000, ctr_median=4.0, ctr_mad=0.53, cvr_median=g.cvr_med or 3.0,
                  cvr_mad=max(0.3, 0.15 * (g.cvr_med or 3.0)), returns_pct=cat["ret"], rto_pct=cat["rto"], delivery_days_p75=6)
    if len(rows) >= 2:
        weeks = [Week(r.impressions, r.clicks, r.orders, (r.returns / r.delivered * 100) if r.delivered else p.ret,
                      (r.rto / max(1, r.orders) * 100)) for r in rows[-2:]]
        stock_units = rows[-1].stock_units
        dd = rows[-1].delivery_days or 4.5
    else:
        clicks = js_round(g.views * g.ctr / 100)
        wk = Week(g.views, clicks, js_round(clicks * g.cvr / 100), p.ret, p.rto)
        weeks, stock_units, dd = [wk, wk], None, 4.5
    return DiagnoseInput(p.id, weeks, peers, p.live_price, list(p.band), g.doi, dd, stock_units=stock_units)


def t_diagnose(ctx: ToolContext, product_id: Optional[str] = None) -> dict:
    p = ctx.product(product_id)
    d = diagnose(_diag_input(ctx, p))
    d["product"] = p.name
    d["source"] = "Diagnosis engine · 8 signals, price checked last"
    return d


def t_lifecycle(ctx: ToolContext, product_id: Optional[str] = None) -> dict:
    p = ctx.product(product_id)
    s = life_story(p)
    f = floor_for(p)
    return {"product": p.name, "stage_now": p.signals.stage, "days_since_launch": p.signals.day,
            "days_of_stock": p.signals.doi, "kept_unit_trend_4w_pct": p.signals.g,
            "planned_path": [{"day": e[0], "price": e[1], "event": e[2]} for e in s["events"]],
            "rival_test": {k: s["rival_test"][k] for k in s["rival_test"] if k in ("decision", "profit_if_match", "profit_if_hold", "orders_if_match", "orders_if_hold")},
            "markdown_ladder": s["markdowns"], "floor_F": f.F, "recovery_floor_exit_only": f.recovery_floor,
            "exit_options": exit_options(f), "category_life": s["category_life"],
            "rule": "Markdowns in ≤ 8% steps, never below F; below F only at Exit with the seller's consent.",
            "source": f"Lifecycle engine ({p.signals.stage})"}


def t_stock_plan(ctx: ToolContext, product_id: Optional[str] = None, daily_demand: Optional[float] = None,
                 lead_time_days: float = 7, stock_units: Optional[float] = None) -> dict:
    p = ctx.product(product_id)
    f = floor_for(p)
    d = float(daily_demand) if daily_demand else round(demand(p, p.live_price), 1)
    rop = inventory.reorder_point(daily_demand=d, lead_time_days=float(lead_time_days or 7), sd_per_day=max(1.0, round(d * 0.34, 1)))
    rop = {k: (round(v, 1) if isinstance(v, float) else v) for k, v in rop.items()}
    stock = stock_units if stock_units is not None else round(p.signals.doi * d)
    out = {"product": p.name, "units_per_day_used": d, "stock_units_est": stock, "days_of_stock": p.signals.doi, **rop,
           "unit_cost": p.cs, "cash_needed": js_round(rop["order_quantity"] * p.cs),
           "reorder_now": stock <= rop["reorder_point"], "stuck": p.signals.doi > 60,
           "source": "Inventory engine · ROP = d × L + z·σ·√L"}
    if p.signals.doi > 60:
        out["stuck_plan"] = "Bundle first, then markdown in ≤ 8% steps above F (see lifecycle tool)."
    return out


def t_pooled_procurement(ctx: ToolContext, sellers_committed: Optional[int] = None) -> dict:
    r = inventory.pooled_procurement(sellers_committed=sellers_committed)
    r["status"] = "ProfitPilot 2.0 preview: needs seller opt-in"
    r["source"] = "Pooled procurement (2.0)"
    return r


def t_credit(ctx: ToolContext, avg_monthly_payout: float = 60000, next_stock_order: Optional[float] = None,
             return_rate_pct: Optional[float] = None) -> dict:
    p = ctx.product(None)
    r = inventory.credit_limit(avg_monthly_payout=float(avg_monthly_payout), next_stock_order=next_stock_order,
                               return_rate_pct=float(return_rate_pct if return_rate_pct is not None else p.ret))
    r["status"] = "ProfitPilot 2.0 preview: regulated NBFC decides; needs consent"
    r["source"] = "Credit sizing (2.0, M30)"
    return r


def t_first_price(ctx: ToolContext, product_id: Optional[str] = None) -> dict:
    p = ctx.product(product_id)
    fp = first_price(p)
    return {k: fp[k] for k in ("start_price", "no_return_price", "gap", "safe_minimum", "market", "warning", "modes",
                                "dual_price", "expected", "why")} | {"product": p.name, "floor_F": fp["floor"]["F"],
                                                                     "source": "First-price engine"}


def t_goal_modes(ctx: ToolContext, product_id: Optional[str] = None) -> dict:
    p = ctx.product(product_id)
    f = floor_for(p)
    return {"current_mode": ctx.mode, "modes": {k: {**v, "price_for_product": mode_price(f, k)} for k, v in MODES.items()},
            "product": p.name, "note": "The seller switches mode at the top of the app; the Coach never changes it.",
            "source": "Goal modes"}


def t_ads_check(ctx: ToolContext, product_id: Optional[str] = None, ad_cost_per_kept_order: Optional[float] = None) -> dict:
    p = ctx.product(product_id)
    f = floor_for(p)
    in_floor = f.other_parts.get("ads", 0)
    margin = p.live_price - f.F
    out = {"product": p.name, "ads_cost_in_floor_per_kept_order": in_floor, "profit_per_kept_order": margin,
           "rule": "Ads make sense only while ad ₹ per kept order stays below your profit per kept order.",
           "source": "Cost branch · ads in floor"}
    if ad_cost_per_kept_order is not None:
        extra = float(ad_cost_per_kept_order) - in_floor
        out["with_new_ad_cost"] = {"ad_cost_per_kept_order": ad_cost_per_kept_order, "new_floor": js_round(f.F + max(0, extra)),
                                   "profit_per_kept_order": js_round(margin - max(0, extra)),
                                   "worth_it": margin - max(0, extra) > 0}
    return out


_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Pow: operator.pow, ast.USub: operator.neg, ast.UAdd: operator.pos, ast.Mod: operator.mod}


def _eval(node):
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        if isinstance(node.op, ast.Pow) and abs(_eval(node.right)) > 8:
            raise ToolError("exponent too large")
        return _OPS[type(node.op)](_eval(node.left), _eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.operand))
    raise ToolError("only + − × ÷ and brackets are allowed")


def t_calculate(ctx: ToolContext, expression: str) -> dict:
    expr = (expression or "").replace("×", "*").replace("÷", "/").replace("−", "-").replace(",", "").replace("₹", "")
    if len(expr) > 120:
        raise ToolError("expression too long")
    try:
        v = _eval(ast.parse(expr, mode="eval"))
    except (SyntaxError, ZeroDivisionError) as e:
        raise ToolError(f"cannot calculate: {e}") from e
    return {"expression": expression, "result": round(v, 2), "source": "Calculator"}


def t_propose_action(ctx: ToolContext, product_id: Optional[str] = None, mode: Optional[str] = None) -> dict:
    p = ctx.product(product_id)
    m = mode if mode in MODES else ctx.mode
    r = recommend(p, m)
    if r["kind"] in ("hold",):
        return {"card": None, "reason": f"No move this week: {r['h']}.", "source": "Lifecycle engine"}
    card = {"key": r["key"], "product_id": p.id, "product": p.name, "kind": r["kind"], "from": r["from"], "to": r["to"],
            "headline": r["h"], "what": r["what"], "why": (r.get("why") or [])[:5], "effect": r.get("eff"),
            "confidence": r["conf"], "undo": r.get("undo"), "checks": r.get("pf")}
    return {"card": card, "note": "Shown to the seller as a Yes / No card. Nothing changes until the seller taps Yes; "
                                  "the server re-checks the guardrails then.", "source": "Lifecycle engine · card"}


# ---------------------------------------------------------------- registry + declarations
S, N, I = "STRING", "NUMBER", "INTEGER"
PID = {"product_id": {"type": S, "description": "Product id (e.g. kurti, lunch, serum, romper, vase). Omit for the selected product."}}


def _decl(name: str, desc: str, props: Optional[dict] = None, required: Optional[List[str]] = None) -> dict:
    d: Dict[str, Any] = {"name": name, "description": desc}
    if not props:           # Gemini rejects an OBJECT schema with empty properties: no-arg tools omit `parameters`
        return d
    d["parameters"] = {"type": "OBJECT", "properties": props}
    if required:
        d["parameters"]["required"] = required
    return d


TOOLS: Dict[str, Callable[..., dict]] = {
    "list_products": t_list_products, "product_overview": t_product_overview, "floor_breakdown": t_floor_breakdown,
    "recommendation": t_recommendation, "simulate_price": t_simulate_price, "best_price": t_best_price,
    "competition": t_competition, "returns_rto": t_returns_rto, "diagnose": t_diagnose, "lifecycle": t_lifecycle,
    "stock_plan": t_stock_plan, "pooled_procurement": t_pooled_procurement, "credit_estimate": t_credit,
    "first_price": t_first_price, "goal_modes": t_goal_modes, "ads_check": t_ads_check, "calculate": t_calculate,
    "propose_action": t_propose_action,
}

DECLARATIONS: List[dict] = [
    _decl("list_products", "All of this seller's products with live price, floor F, profit per kept order, stage and stock."),
    _decl("product_overview", "One product: live price, floor, profit per kept order and per day, orders/day estimate, funnel signals, stage, stock.", PID),
    _decl("floor_breakdown", "How the return-adjusted floor F is built: every cost, kept rate, per-100-orders funnel, safety margin.", PID),
    _decl("recommendation", "This week's engine recommendation (raise / hold / markdown / dual price) with reasons, ₹ effect, confidence and pre-flight checks.",
          {**PID, "mode": {"type": S, "description": "Goal mode: cash, growth, margin or clear. Omit for the seller's current mode."}}),
    _decl("simulate_price", "What happens at a given price: orders/day, kept/day, profit per kept order and per day, loss warning, dual price.",
          {**PID, "price": {"type": N, "description": "Price in rupees to test"}}, ["price"]),
    _decl("best_price", "The profit-maximising price on the demand curve, compared with the live price.", PID),
    _decl("competition", "Look-alike listings: count, price band, median, cheapest close rival and the loss if the seller copied it.", PID),
    _decl("returns_rto", "Returns and RTO for a product: rates vs category, cost per kept order, reason split, who can fix what, dual price.", PID),
    _decl("diagnose", "Why orders are low: the 8-signal funnel scan (views, clicks, conversion, returns, RTO, stock, delivery, price last) with the one fix to do first.", PID),
    _decl("lifecycle", "Lifecycle stage, planned price path, rival test, markdown ladder and exit options.", PID),
    _decl("stock_plan", "Reorder point, order quantity and cash needed; flags stuck stock (> 60 days).",
          {**PID, "daily_demand": {"type": N, "description": "Units sold per day, if the seller said it"},
           "lead_time_days": {"type": N, "description": "Supplier lead time in days, default 7"},
           "stock_units": {"type": N, "description": "Units in stock now, if the seller said it"}}),
    _decl("pooled_procurement", "2.0 preview: group buying with other sellers to reach a supplier's minimum order and a lower unit price.",
          {"sellers_committed": {"type": I, "description": "How many sellers have firmly committed"}}),
    _decl("credit_estimate", "2.0 preview: indicative working-capital limit from payouts, next stock order and return rate (NBFC decides).",
          {"avg_monthly_payout": {"type": N, "description": "Average monthly payout in rupees"},
           "next_stock_order": {"type": N, "description": "Value of the next stock order in rupees"},
           "return_rate_pct": {"type": N, "description": "Return rate %"}}),
    _decl("first_price", "Launch price for a product: floor + target profit, market band, dual price and price per goal mode.", PID),
    _decl("goal_modes", "The four goal modes (cash, growth, margin, clear), what each optimises and the price each gives for this product.", PID),
    _decl("ads_check", "Whether ads pay for themselves: ad cost per kept order vs profit per kept order.",
          {**PID, "ad_cost_per_kept_order": {"type": N, "description": "Ad spend per kept order the seller is considering"}}),
    _decl("calculate", "Arithmetic on numbers you already got from tools (e.g. 60 * 23 * 0.78). Use it instead of mental maths.",
          {"expression": {"type": S, "description": "e.g. (384-309)*20*0.78"}}, ["expression"]),
    _decl("propose_action", "Turn the engine's recommendation into a Yes / No card for the seller. Use when the seller asks what to do or wants to change a price. It does NOT change anything.",
          {**PID, "mode": {"type": S, "description": "Goal mode, optional"}}),
]


def run_tool(ctx: ToolContext, name: str, args: Optional[dict]) -> dict:
    fn = TOOLS.get(name)
    if not fn:
        return {"error": f"unknown tool {name!r}"}
    args = {k: v for k, v in (args or {}).items() if v is not None and v != ""}
    try:
        return fn(ctx, **args)
    except ToolError as e:
        return {"error": str(e)}
    except TypeError as e:
        return {"error": f"bad arguments for {name}: {e}"}
