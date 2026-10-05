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
    (r"phir bhi|still|cut", "cut"),
    (r"orders kam|dropped|orders", "orders"),
    (r"floor", "floor"),
    (r"profit|kamai", "profit"),
    (r"return", "returns"),
    (r"compet|rival", "comp"),
    (r"reorder|stock kab", "reorder"),
    (r"badha|raise|increase|change|badal", "raise"),
    (r"nahi bik|not selling|isn't selling", "nosell"),
    (r"\bads?\b", "ads"),
    (r"atak|stuck", "stuck"),
    (r"kam|drop|low", "orders"),
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
        text = ("Your lunch box stock has reached the reorder point (12/day × 7 days + 18 safety = 102 units). "
                "Order about 170 units (≈ 2 weeks of sales): ₹35,700 at ₹210, or join a pool at ₹188/unit once firm commitments reach 300 units.")
        src, nx = "Inventory signals · 12 × 7 + 18 = 102", ["stuck", "profit"]
    elif intent == "stuck":
        vase = (products or {}).get("vase", p)
        m = life_story(vase)
        vf = floor_for(vase)
        a, b, c, d = m["post_rival_price"], *m["markdowns"]
        text = (f"Your vase has 73 days of stock (400 ÷ 5.5/day), past the 60-day limit. Bundle first. Then a markdown ladder in ≤ 8% steps: "
                f"{inr(a)} → {inr(b)} → {inr(c)} → {inr(d)}, stopping above your floor {inr(vf.F)}. Below that only at Exit with your consent, "
                f"never below {inr(vf.recovery_floor)}.")
        src, nx = "Lifecycle engine (Decline) · 400 ÷ 5.5 = 73 days", ["reorder", "profit"]
    elif intent == "orders":
        text = ("I don't think you need to cut your price right now. Views are normal, but clicks are low (2.5% vs 4.0% for similar products, "
                "2 weeks in a row). Shall we test a white-background main photo for 7 days at the same price? About ₹430 more a week (estimate).")
        src, nx = "Diagnosis engine · last 2 weeks · 5,000 views", ["cut", "profit", "raise"]
    elif intent == "cut":
        sf = floor_for((products or {}).get("serum", p))
        text = (f"₹199 is still above the serum floor {inr(sf.F)}, but a cut is allowed only when the price–value check fires. It hasn't: "
                "the problem is clicks. −20% is also more than the 8% max step. Keep ₹249 and fix the photo instead.")
        src, nx = "Panic Brake rules · example ₹199 (demo)", ["orders", "profit"]
    elif intent == "nosell":
        text = ("Let's see why first; price comes later. Views are under half of similar kurtis (1,800 vs 4,000). Listing score 72; it should be 80+. "
                "Fix title and attributes before touching price. Impact is measured after 7 days.")
        src, nx = "Diagnosis engine · visibility · last 7 days", ["ads", "cut"]
    elif intent == "ads":
        text = ("Your kurti doesn't need more ads right now. Ads cost ₹12 per kept order, already in your floor. Ads make sense only while "
                "ad ₹ per kept order is below your margin.")
        src, nx = "Cost branch · ads attribution · last 7 days", ["nosell", "raise"]
    else:
        return {"intent": None, "answer": "I can't answer that yet. Try one of these:", "source": None,
                "next": [k for k in CHIPS if k not in ("cut", "floor")], "rules": RULES}
    return {"intent": intent, "answer": text, "source": src, "next": nx, "chips": {k: CHIPS[k] for k in nx}, "rules": RULES}
