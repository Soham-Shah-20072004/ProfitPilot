"""Diagnose before you discount: an 8-signal funnel scan, price checked last.

Each signal is compared with similar products. Click-through and conversion
use a robust z-score, (value − median) ÷ (1.4826 × MAD) across similar
listings, and must stay below −1.5 for 2 weeks in a row before a card is
shown. Conversion needs at least 300 clicks a week to be judged at all.
The first signal that fires is the bottleneck; only the price signal (or low
conversion with a price above the band) can lead to a price move.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from .fmt import group_in, inr, js_round, to_fixed

Z_LIMIT = -1.5
MIN_CLICKS_FOR_CVR = 300

NODES = [
    ("views", "Views", "Impressions vs similar products", "fires if < 50% of similar products, 2 weeks in a row"),
    ("clicks", "Clicks (CTR)", "Thumbnail / title", "fires if CTR z < −1.5, 2 weeks in a row"),
    ("conv", "Conversion", "Price–value, page trust", "fires if CVR z < −1.5, 2 weeks in a row (needs ≥ 300 clicks a week)"),
    ("ret", "Returns", "Size, quality, expectation", "fires if returns > category + 5 points"),
    ("rto", "RTO", "COD refusals, failed delivery", "fires if RTO > category + 5 points"),
    ("stock", "Stock", "Days of inventory", "fires if stock is below the reorder point (or < 7 days), or > 60 days"),
    ("del", "Delivery", "Dispatch speed", "fires if delivery days > p75 of similar products"),
    ("price", "Price", "Band position", "checked LAST; fires if outside the look-alike band"),
]


@dataclass
class Week:
    impressions: int
    clicks: int
    orders: int
    returns_pct: float
    rto_pct: float


@dataclass
class Peers:
    impressions_median: float
    ctr_median: float
    ctr_mad: float
    cvr_median: float
    cvr_mad: float
    returns_pct: float
    rto_pct: float
    delivery_days_p75: float


@dataclass
class DiagnoseInput:
    product_id: str
    weeks: List[Week]                 # last two weeks, oldest first
    peers: Peers
    price: int
    band: List[int]
    stock_days: float
    delivery_days: float
    stock_units: Optional[float] = None
    reorder_point: Optional[float] = None
    question: str = "Orders are low. Should I cut my price?"


def robust_z(x: float, median: float, mad: float) -> float:
    scale = 1.4826 * mad
    return (x - median) / scale if scale > 0 else 0.0


def diagnose(inp: DiagnoseInput) -> dict:
    w = inp.weeks[-2:] if len(inp.weeks) >= 2 else inp.weeks
    pe = inp.peers
    last = w[-1]
    ctr = [x.clicks / x.impressions * 100 if x.impressions else 0 for x in w]
    cvr = [x.orders / x.clicks * 100 if x.clicks else 0 for x in w]
    zc = [robust_z(c, pe.ctr_median, pe.ctr_mad) for c in ctr]
    zv = [robust_z(c, pe.cvr_median, pe.cvr_mad) for c in cvr]
    lo, hi = inp.band
    in_band = lo <= inp.price <= hi

    results: Dict[str, dict] = {}
    results["views"] = {"bad": all(x.impressions < 0.5 * pe.impressions_median for x in w),
                        "value": f"{group_in(last.impressions)} views/week vs {group_in(round(pe.impressions_median))} similar"}
    results["clicks"] = {"bad": all(z < Z_LIMIT for z in zc),
                         "value": f"CTR {to_fixed(ctr[-1], 1)}% vs {to_fixed(pe.ctr_median, 1)}% (z = {to_fixed(zc[-1], 1)})"}
    enough = all(x.clicks >= MIN_CLICKS_FOR_CVR for x in w)
    results["conv"] = {"bad": enough and all(z < Z_LIMIT for z in zv), "insufficient": not enough,
                       "value": (f"Conversion {to_fixed(cvr[-1], 1)}% vs {to_fixed(pe.cvr_median, 1)}% (z = {to_fixed(zv[-1], 1)})"
                                 if enough else f"Conversion {to_fixed(cvr[-1], 1)}%: only {last.clicks} clicks, not judged (needs ≥ 300)")}
    results["ret"] = {"bad": last.returns_pct > pe.returns_pct + 5,
                      "value": f"Returns {to_fixed(last.returns_pct, 0)}% vs category {to_fixed(pe.returns_pct, 0)}%"}
    results["rto"] = {"bad": last.rto_pct > pe.rto_pct + 5,
                      "value": f"RTO {to_fixed(last.rto_pct, 0)}% vs category {to_fixed(pe.rto_pct, 0)}%"}
    low_stock = (inp.stock_units is not None and inp.reorder_point is not None and inp.stock_units < inp.reorder_point) or inp.stock_days < 7
    results["stock"] = {"bad": low_stock or inp.stock_days > 60, "low": low_stock,
                        "value": f"{to_fixed(inp.stock_days, 0)} days of stock" + (
                            f" · {to_fixed(inp.stock_units, 0)} units vs reorder point {to_fixed(inp.reorder_point, 0)}"
                            if inp.reorder_point is not None and inp.stock_units is not None else "")}
    results["del"] = {"bad": inp.delivery_days > pe.delivery_days_p75,
                      "value": f"Delivery {to_fixed(inp.delivery_days, 1)} days vs p75 {to_fixed(pe.delivery_days_p75, 1)}"}
    results["price"] = {"bad": not in_band,
                        "value": f"{inr(inp.price)} {'inside' if in_band else ('above' if inp.price > hi else 'below')} the "
                                 f"{inr(lo)}–{inr(hi)} look-alike band"}

    nodes, bottleneck = [], None
    for key, label, checks, rule in NODES:
        res = results[key]
        if bottleneck:
            status = "skipped"
        elif res.get("insufficient"):
            status = "insufficient"
        else:
            status = "bad" if res["bad"] else "ok"
        if status == "bad" and not bottleneck:
            bottleneck = key
        nodes.append({"key": key, "label": label, "checks": checks, "rule": rule, "status": status, "value": res["value"]})

    action, diagnosis, price_move = _playbook(bottleneck, inp, in_band, results)
    return {"product_id": inp.product_id, "question": inp.question, "bottleneck": bottleneck, "nodes": nodes,
            "diagnosis": diagnosis, "action": action, "price_move_allowed": price_move,
            "rule": "First signal that fires is the bottleneck; fix one thing at a time, largest ₹ loss first. Price is checked last.",
            "confidence": "Medium", "confidence_why": "Weekly signal vs similar products; a card shows only if it holds 2 weeks in a row."}


def _playbook(key: Optional[str], inp: DiagnoseInput, in_band: bool, res: dict):
    lo, hi = inp.band
    if key is None:
        return "Hold the price and scale; no discount needed.", "All eight signals are healthy.", False
    if key == "views":
        return ("Fix title and attributes, check the category mapping; small ad boost only while ad ₹ per kept order < margin.",
                "Buyers are not finding the listing.", False)
    if key == "clicks":
        return "Test a white-background main image for 7 days.", "Price is probably not the main problem: people see it but don't click.", False
    if key == "conv":
        if in_band:
            return ("Add a size chart, 3 customer photos and a clear delivery promise.",
                    "People click but don't buy, and the price is inside the band: fix page and trust, not price.", False)
        return (f"Step the price toward the {inr(lo)}–{inr(hi)} band in ≤ 8% moves, 7 days apart.",
                "People click but don't buy, and the price is outside the band.", True)
    if key == "ret":
        return "Add a size chart and fix the description; check quality before dispatch.", "Orders come in, but too many come back.", False
    if key == "rto":
        return "Turn on WhatsApp COD confirmation and a prepaid nudge before dispatch.", "COD refusals are reducing kept orders.", False
    if key == "stock":
        if res["stock"].get("low"):
            return "Reorder now (2.0 reorder alert). Do not discount.", "Demand exists, but stock is about to limit sales.", False
        return "Bundle first, then a markdown ladder in ≤ 8% steps (see Lifecycle).", "Stock is stuck: more than 60 days of inventory.", False
    if key == "del":
        return "Dispatch within 24 h and book an earlier pickup slot.", "Slow fulfilment is hurting rank and conversion.", False
    # price
    if inp.price > hi:
        step = js_round(inp.price * 0.936)
        return (f"Step down {inr(inp.price)} → about {inr(step)} now (≤ 8%), then again after the 7-day cooldown, never below the floor.",
                "Your price is above the competitive range.", True)
    return (f"Raise toward {inr(lo)} (the band's lower end) in ≤ 8% steps.", "Your price is below the competitive range: you leave money on the table.", True)


# ---------------------------------------------------------------- demo cases (same stories as the app's Diagnose screen)
def _w(imp, ctr, cvr, ret, rto):
    clicks = js_round(imp * ctr / 100)
    return Week(impressions=imp, clicks=clicks, orders=js_round(clicks * cvr / 100), returns_pct=ret, rto_pct=rto)


def demo_cases() -> Dict[str, DiagnoseInput]:
    fashion = Peers(impressions_median=4000, ctr_median=4.0, ctr_mad=0.53, cvr_median=3.0, cvr_mad=0.6,
                    returns_pct=12, rto_pct=8, delivery_days_p75=6)
    return {
        "cat": DiagnoseInput("serum", [_w(5000, 2.5, 12, 4, 9), _w(5000, 2.5, 12, 4, 9)],
                             Peers(4000, 4.0, 0.53, 12.0, 2.0, 4, 9, 6), 249, [199, 299], 40, 4.5),
        "price": DiagnoseInput("kurti", [_w(4200, 4.1, 1.4, 12, 8), _w(4200, 4.1, 1.4, 12, 8)], fashion, 469, [329, 429], 38, 4.5),
        "conv": DiagnoseInput("kurti", [_w(8000, 4.1, 1.1, 12, 8), _w(8000, 4.1, 1.1, 12, 8)], fashion, 369, [329, 429], 35, 4.5),
        "ret": DiagnoseInput("romper", [_w(4200, 4.2, 3.2, 19, 8), _w(4200, 4.2, 3.2, 19, 8)],
                             Peers(4000, 4.0, 0.53, 3.0, 0.6, 10, 8, 6), 349, [299, 399], 32, 4.5),
        "rto": DiagnoseInput("kurti", [_w(4200, 4.1, 3.2, 12, 19), _w(4200, 4.1, 3.2, 12, 19)], fashion, 369, [329, 429], 35, 4.5),
        "inv": DiagnoseInput("lunch", [_w(4100, 3.9, 3.0, 5, 7), _w(4100, 3.9, 3.0, 5, 7)],
                             Peers(4000, 4.0, 0.53, 3.0, 0.6, 5, 7, 6), 449, [379, 499], 8, 4.5, stock_units=96, reorder_point=102),
        "ful": DiagnoseInput("vase", [_w(4200, 4.0, 3.0, 9, 8), _w(4200, 4.0, 3.0, 9, 8)],
                             Peers(4000, 4.0, 0.53, 3.0, 0.6, 9, 8, 6), 499, [449, 599], 40, 8),
    }
