"""Plain-language "Why?" for the engine's weekly card, in English, Hinglish or Hindi.

The engine decides the move and its numbers; Gemini only rewrites the reasons
in simple words. The grounding guard rejects any number the card does not
contain, and the template "Why" is used instead.
"""
from __future__ import annotations

from typing import Optional

from ..engine.catalog import MODES, Product as EProduct
from ..engine.floor import floor_for
from ..engine.recommend import recommend
from . import guard
from .llm import LLM, LLMError
from .provider import get_llm, note_error

SCHEMA = {"type": "OBJECT", "properties": {
    "headline": {"type": "STRING"}, "why": {"type": "ARRAY", "items": {"type": "STRING"}},
    "effect": {"type": "STRING"}, "risk": {"type": "STRING"}, "undo": {"type": "STRING"}},
    "required": ["headline", "why", "effect", "undo"]}

SYSTEM = """Rewrite a pricing suggestion for a small Indian seller who is new to online selling.
Use ONLY the facts given. Copy numbers exactly; do not compute new numbers. No jargon (say "lowest safe price" for floor F,
"buyers who keep the order" for kept orders). headline: one line, what to do. why: 2–4 short bullets. effect: one line on
money, say "about" / "expected". risk: one line on what could go wrong. undo: one line.
Language: {lang}."""

LANGS = {"en": "simple English", "hi": "Hindi in Devanagari script", "hinglish": "Hinglish (Hindi in Roman script)"}


def template(card: dict) -> dict:
    return {"headline": card["h"], "why": card.get("why") or [], "effect": card.get("eff") or "", "risk": None,
            "undo": card.get("undo") or ""}


def explain(p: EProduct, mode: str, lang: str = "en", llm: Optional[LLM] = None) -> dict:
    mode = mode if mode in MODES else "growth"
    card = recommend(p, mode)
    f = floor_for(p)
    base = {"product": p.name, "mode": mode, "card_key": card["key"], "kind": card["kind"], "from": card["from"], "to": card["to"],
            "confidence": card["conf"], "checks": card.get("pf"), "floor_F": f.F}
    llm = llm or get_llm()
    if llm is None:
        return {**base, **template(card), "engine": "rules", "grounded": True, "fallback_reason": "AI not configured (no GEMINI_API_KEY)"}
    facts = {"product": p.name, "headline": card["h"], "what": card["what"], "why": card.get("why"), "effect": card.get("eff"),
             "undo": card.get("undo"), "confidence": card["conf"], "confidence_why": card["confWhy"], "floor_F": f.F,
             "live_price": p.live_price, "checks": card.get("pf")}
    try:
        out = llm.json(SYSTEM.format(lang=LANGS.get(lang, LANGS["en"])), f"FACTS: {facts}", SCHEMA)
        text = " ".join([out.get("headline", ""), *(out.get("why") or []), out.get("effect", ""), out.get("risk") or "", out.get("undo", "")])
        bad = guard.ungrounded(text, [facts])
        if bad:
            return {**base, **template(card), "engine": "rules", "grounded": True,
                    "fallback_reason": f"AI wording used numbers not in the card: {', '.join(f'{b:g}' for b in bad[:5])}"}
        return {**base, "headline": out.get("headline") or card["h"], "why": out.get("why") or card.get("why") or [],
                "effect": out.get("effect") or card.get("eff"), "risk": out.get("risk"), "undo": out.get("undo") or card.get("undo"),
                "engine": llm.name, "grounded": True, "fallback_reason": None}
    except (LLMError, ValueError, TypeError, AttributeError) as e:
        note_error("explain", e)
        return {**base, **template(card), "engine": "rules", "grounded": True, "fallback_reason": f"AI unavailable: {e}"}
