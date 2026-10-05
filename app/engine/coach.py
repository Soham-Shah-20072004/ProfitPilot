"""Coach: routes a seller's question (Hindi or English) to an engine answer.

Rule-based today (keyword routing, same order as the app). Every answer is
composed from engine numbers and names its source; the Coach never changes a
price, a stock order or a mode by itself. An LLM can replace route() later
behind the same interface.
"""
from __future__ import annotations

import re
from typing import Optional

from .catalog import Product
from .demand import confidence_from_views, demand
from .floor import floor_for
from .fmt import inr, js_round, to_fixed
from .lifecycle import life_story
from .recommend import recommend

ROUTES = [
    (r"phir bhi|still|cut|ghata|घटा|कम कर", "cut"),
    (r"orders kam|dropped|orders|ऑर्डर", "orders"),
    (r"floor|फ्लोर|lagat|लागत", "floor"),
    (r"profit|kamai|munafa|मुनाफ|कमाई|फायदा", "profit"),
    (r"return|wapas|वापस|रिटर्न|rto", "returns"),
    (r"compet|rival|dusre seller|प्रतिस्पर्ध", "comp"),
    (r"reorder|stock kab|kitna maal|स्टॉक कब|दोबारा मंग", "reorder"),
    (r"badha|raise|increase|change|badal|बढ़ा|बदल", "raise"),
    (r"nahi bik|not selling|isn't selling|नहीं बिक", "nosell"),
    (r"\bads?\b|vigyapan|विज्ञापन|ऐड", "ads"),
    (r"atak|stuck|pada hai|अटका|पड़ा", "stuck"),
    (r"kam|drop|low|कम", "orders"),
]

CHIPS = {"orders": "My orders dropped", "profit": "How much profit?", "returns": "Why are returns coming?",
         "comp": "What are competitors doing?", "raise": "Can I change my price?", "reorder": "When to reorder stock?",
         "nosell": "My product isn't selling", "ads": "Should I run ads?", "stuck": "My stock is stuck",
         "cut": "I still want to cut price", "floor": "How is my floor made?"}

RULES = ["I never change a price, stock order or mode: only your tap does.",
         "Every number comes from a ProfitPilot engine; the source is shown.",
         "If data is thin, I say so (confidence Low).",
         "I never show another seller's details.",
         "Money actions are tap-only, never voice.",
         "Expected means expected: no guarantees."]


def signals_diag_input(p: Product):
    """Diagnose input built from a product's weekly signals (used when no metric weeks were posted)."""
    from .catalog import CATEGORIES
    from .diagnose import DiagnoseInput, Peers, Week
    g, cat = p.signals, CATEGORIES[p.category]
    clicks = js_round(g.views * g.ctr / 100)
    wk = Week(g.views, clicks, js_round(clicks * g.cvr / 100), p.ret, p.rto)
    peers = Peers(4000, 4.0, 0.53, g.cvr_med or 3.0, max(0.3, 0.15 * (g.cvr_med or 3.0)), cat["ret"], cat["rto"], 6)
    return DiagnoseInput(p.id, [wk, wk], peers, p.live_price, list(p.band), g.doi, 4.5)


def route(text: str) -> Optional[str]:
    x = (text or "").lower()
    for pattern, intent in ROUTES:
        if re.search(pattern, x):
            return intent
    return None


def answer(intent: str, p: Product, mode: str = "growth", products: Optional[dict] = None) -> dict:
    f = floor_for(p)
    price = p.live_price
    nx = []
    if intent == "profit":
        o = demand(p, price)
        d = o * f.k * (price - f.F)
        text = (f"At {inr(price)} you earn {inr(price - f.F)} on every {p.name.lower()} that stays sold. About {js_round(o)} orders a day; "
                f"{js_round(f.k * 100)}% stay sold. About {inr(d)} a day (estimate, confidence {confidence_from_views(p.signals.views).lower()}). "
                f"You may think you make {inr(price - f.cs)} a piece; after returns, RTO and other costs it is {inr(price - f.F)}.")
        src, nx = f"Floor engine F {inr(f.F)} · k {f.k:.2f} · {to_fixed(o, 1)} orders/day", ["floor", "raise", "ads"]
    elif intent == "floor":
        text = (f"F = {inr(f.F)}: sourcing {f.cs:g} + pack {f.pack:g} + delivery {f.fwd:g} + return {f.c_ret} + RTO {f.c_rto} + other {f.other}. "
                f"Return + RTO together = {inr(f.B)} per kept order. Safety margin {inr(f.safety_margin)} → safe minimum {inr(f.safe_minimum)}.")
        src, nx = "Floor engine", ["profit", "returns"]
    elif intent == "returns":
        text = (f"{f.returns_n} of 100 placed {p.name.lower()} orders come back (return rate {f.ret:g}%), and {f.rto_n} of 100 are refused "
                f"at the door (RTO). RTO is mostly Meesho-side: COD confirmation, prepaid nudge, delivery. Returns are mostly in your hands: "
                f"size chart, true photos, quality check, packing. Start with the bigger ₹ loss.")
        src, nx = f"Returns & RTO · {p.name} · category prior", ["profit", "comp"]
    elif intent == "comp":
        text = (f"There are {p.lookalikes} listings like yours. Most sell between {inr(p.band[0])} and {inr(p.band[1])}; median {inr(p.median)}. "
                f"Don't copy the cheapest seller: at {inr(p.rival_price)} you'd lose {inr(f.F - p.rival_price)} a piece. "
                "No seller names are shown and prices are never coordinated: your floor is your own.")
        src, nx = f"Live catalogue · {p.lookalikes} look-alikes", ["raise", "ads"]
    elif intent == "raise":
        r = recommend(p, mode, f)
        text = (f"Not right now: {r['h']}." if r["kind"] == "hold" else f"Yes: {r['h']}.") + f" Goal mode: {mode.upper()}."
        src, nx = f"Lifecycle engine ({p.signals.stage}) · bounded step ≤ 8%", ["profit", "comp"]
    elif intent == "reorder":
        from .inventory import reorder_point
        d = round(demand(p, price), 1)
        rp = reorder_point(daily_demand=d, lead_time_days=7, sd_per_day=max(1.0, round(d * 0.34, 1)))
        stock = js_round(p.signals.doi * d)
        rop, q = js_round(rp["reorder_point"]), rp["order_quantity"]
        if p.signals.doi > 60:
            text = (f"Don't reorder the {p.name.lower()}: you have {p.signals.doi:g} days of stock, past the 60-day limit. "
                    "Sell what you have first (bundle, then small markdowns above your floor).")
        elif stock <= rop:
            text = (f"Yes, reorder the {p.name.lower()} now. About {stock} units in stock vs a reorder point of {rop} "
                    f"({to_fixed(d, 1)}/day × 7 days + {rp['safety_stock']} safety). Order about {q} units (≈ 2 weeks of sales): "
                    f"{inr(q * p.cs)} at {inr(p.cs)} a piece.")
        else:
            text = (f"Not yet. About {stock} units of {p.name.lower()} in stock ({p.signals.doi:g} days); the reorder point is {rop} units "
                    f"({to_fixed(d, 1)}/day × 7 days + {rp['safety_stock']} safety). I'll flag it when stock drops to {rop}.")
        src, nx = f"Inventory engine · {p.name} · ROP = d × L + safety stock", ["stuck", "profit"]
    elif intent == "stuck":
        m = life_story(p)
        a, b, c, d = m["post_rival_price"], *m["markdowns"]
        if p.signals.doi > 60:
            text = (f"Your {p.name.lower()} has {p.signals.doi:g} days of stock, past the 60-day limit. Bundle first. Then a markdown ladder in ≤ 8% steps: "
                    f"{inr(a)} → {inr(b)} → {inr(c)} → {inr(d)}, stopping above your floor {inr(f.F)}. Below that only at Exit with your consent, "
                    f"never below {inr(f.recovery_floor)}.")
            src = f"Lifecycle engine ({p.signals.stage}) · {p.signals.doi:g} days of stock"
        else:
            text = (f"Your {p.name.lower()} stock is not stuck: {p.signals.doi:g} days of stock (limit 60). No markdown needed. "
                    f"If it ever passes 60 days: bundle first, then ≤ 8% steps, never below your floor {inr(f.F)}.")
            src = f"Lifecycle engine ({p.signals.stage}) · days of stock"
        nx = ["reorder", "profit"]
    elif intent in ("orders", "nosell", "cut"):
        from .diagnose import diagnose
        dg = diagnose(signals_diag_input(p))
        bad = [n for n in dg["nodes"] if n["status"] == "bad"]
        if intent == "cut":
            to = js_round(price * 0.92)
            text = (f"A cut is allowed only if the diagnosis points to price, and only in ≤ 8% steps above your floor {inr(f.F)}. "
                    + (f"Diagnosis: {dg['diagnosis']} First fix: {dg['action']}" if dg["bottleneck"] else
                       f"All 8 signals look healthy, so a cut would just give away margin: at {inr(to)} you'd keep {inr(to - f.F)} per kept order instead of {inr(price - f.F)}.")
                    + (" A price step is allowed here." if dg["price_move_allowed"] else " Keep the price for now."))
        elif dg["bottleneck"]:
            text = (f"Let's see why first; price comes later. {bad[0]['label']}: {bad[0]['value']}. {dg['diagnosis']} "
                    f"First fix: {dg['action']} Impact is checked after 7 days.")
        else:
            text = (f"Your {p.name.lower()} looks healthy on all 8 signals ({dg['nodes'][0]['value']}; {dg['nodes'][1]['value']}). "
                    f"No need to cut the price. {dg['action']}")
        src, nx = f"Diagnosis engine · {p.name} · 8 signals, price last", ["profit", "raise", "comp"]
    elif intent == "ads":
        ads = f.other_parts.get("ads", 0)
        margin = price - f.F
        text = (f"Ads already cost {inr(ads)} per kept order for your {p.name.lower()}, inside your floor {inr(f.F)}. "
                f"You keep {inr(margin)} per kept order, so extra ads pay only while extra ad ₹ per kept order stays below {inr(margin)}. "
                + ("Views are low, so a small, capped boost can help." if p.signals.views < 2000 else "Views are fine, so more ads aren't needed right now."))
        src, nx = f"Cost branch · ads inside F · {p.name}", ["nosell", "raise"]
    else:
        return {"intent": None, "answer": "I can't answer that yet. Try one of these:", "source": None,
                "next": [k for k in CHIPS if k not in ("cut", "floor")], "rules": RULES}
    return {"intent": intent, "answer": text, "source": src, "next": nx, "chips": {k: CHIPS[k] for k in nx}, "rules": RULES}
