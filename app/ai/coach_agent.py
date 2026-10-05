"""ProfitPilot Coach on Gemini: free-form questions in Hindi / Hinglish / English,
answered by calling the engine as tools.

Flow: question → Gemini chooses tools → engine computes → Gemini writes a short
answer from those results → grounding guard (every number must come from a
tool) → reply with sources and optional Yes / No cards. Any failure, missing
key or ungrounded answer falls back to the rule-based Coach (engine.coach).
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from ..config import get_settings
from ..engine.coach import CHIPS, RULES, answer as rule_answer, route
from . import guard
from .llm import LLM, LLMError
from .provider import get_llm, note_error
from .tools import DECLARATIONS, ToolContext, run_tool

log = logging.getLogger("profitpilot.ai")

SYSTEM = """You are ProfitPilot Coach, a business helper for small Meesho sellers in India (often new to online selling).
You explain the seller's own ProfitPilot numbers and help them decide. Today: {today}. Seller: {seller_name}.
Selected product: {product} (id {pid}). Goal mode: {mode}. Products: {catalogue}.

HOW TO ANSWER
- Call tools to get numbers. NEVER invent, estimate or recall a number yourself. Every ₹, %, count or day you write must
  appear in a tool result in this conversation. For any arithmetic, call `calculate`.
- If the question is about another product, pass its product_id. If unsure which product, use the selected one.
- If a tool has no data for something, say plainly you don't know yet and what data would answer it (confidence Low).
- When the seller asks what to do, whether to change a price, or agrees to a suggestion, call `propose_action` so they get a
  Yes / No card. You can never change a price, stock order or goal mode yourself: only the seller's tap does. Never say you changed anything.
- Diagnose before discount: if orders are low or the seller wants to cut the price, call `diagnose` first. Price is checked last.
- Never suggest a price below the floor F. Steps are at most 8%, 7 days apart, max 2 moves a month.
- Never reveal or guess other sellers' names or data; never suggest coordinating prices with others.
- 2.0 features (pooled buying, credit, regional stock) are previews that need opt-in; say so.
- Money is never guaranteed: say "expected" / "about" for forecasts.

STYLE
- Reply in the seller's language and script: Hindi in Devanagari if they wrote Devanagari, Hinglish (Roman Hindi) if they wrote
  Hinglish, otherwise English. {lang_hint}
- Simple words, no jargon (say "price you can't go below" for floor if needed). 2 to 6 short sentences, or up to 5 short bullets.
- Lead with the answer (yes / no / do this), then the one or two numbers that matter, then the next step.
- Use ₹ with Indian digit grouping (₹1,23,456). Do not use markdown headings or tables.
"""

MAX_HISTORY = 10
_DEVANAGARI = re.compile(r"[ऀ-ॿ]")


def _lang_hint(text: str, lang: Optional[str]) -> str:
    if _DEVANAGARI.search(text or ""):
        return "The seller wrote in Devanagari: answer in Hindi (Devanagari)."
    if lang == "hi":
        return "The app is set to Hindi: answer in simple Hinglish (Roman script) unless the seller wrote English sentences."
    return ""


def _short(result: Dict[str, Any], limit: int = 280) -> str:
    s = json.dumps(result, ensure_ascii=False, default=str)
    return s if len(s) <= limit else s[:limit] + "…"


def rules_reply(ctx: ToolContext, text: str, intent: Optional[str] = None, reason: Optional[str] = None) -> dict:
    intent = intent or route(text or "")
    p = ctx.products.get(ctx.default_pid)
    if p is None:
        return {"answer": "Add a product first.", "engine": "rules", "intent": None, "grounded": True,
                "sources": [], "tool_calls": [], "cards": [], "next": [], "chips": {}, "rules": RULES, "fallback_reason": reason}
    res = rule_answer(intent or "", p, ctx.mode, ctx.products)
    return {"answer": res["answer"], "engine": "rules", "intent": res.get("intent"), "grounded": True,
            "sources": [res["source"]] if res.get("source") else [], "tool_calls": [], "cards": [],
            "next": res.get("next", []), "chips": {k: CHIPS[k] for k in res.get("next", []) if k in CHIPS},
            "rules": RULES, "fallback_reason": reason}


def _history_contents(history: Optional[List[dict]]) -> List[dict]:
    out = []
    for h in (history or [])[-MAX_HISTORY:]:
        role = "model" if h.get("role") in ("model", "assistant", "coach") else "user"
        txt = str(h.get("text") or "")[:1500]
        if txt:
            out.append({"role": role, "parts": [{"text": txt}]})
    # Gemini wants the conversation to start with a user turn
    while out and out[0]["role"] != "user":
        out.pop(0)
    return out


def chat(ctx: ToolContext, message: str, history: Optional[List[dict]] = None, lang: Optional[str] = None,
         llm: Optional[LLM] = None) -> dict:
    llm = llm or get_llm()
    if llm is None:
        return rules_reply(ctx, message, reason="AI not configured (no GEMINI_API_KEY)")

    s = get_settings()
    p = ctx.products.get(ctx.default_pid)
    system = SYSTEM.format(
        today=ctx.t.date().isoformat(), seller_name=getattr(ctx.seller, "name", "seller"),
        product=p.name if p else "none", pid=ctx.default_pid, mode=ctx.mode.upper(),
        catalogue=", ".join(f"{k} = {v.name}" for k, v in ctx.products.items()),
        lang_hint=_lang_hint(message, lang))
    contents = _history_contents(history) + [{"role": "user", "parts": [{"text": message}]}]

    facts: List[Any] = []
    calls_log: List[dict] = []
    sources: List[str] = []
    cards: List[dict] = []
    try:
        reply = None
        for _ in range(max(1, s.ai_max_tool_rounds)):
            reply = llm.generate(system, contents, tools=DECLARATIONS, temperature=0.3)
            if not reply.calls:
                break
            contents.append({"role": "model", "parts": reply.parts})       # echo exactly (thought signatures)
            responses = []
            for c in reply.calls:
                res = run_tool(ctx, c["name"], c.get("args"))
                facts.append(res)
                calls_log.append({"name": c["name"], "args": c.get("args") or {}, "result": _short(res)})
                if res.get("source") and res["source"] not in sources:
                    sources.append(res["source"])
                if c["name"] == "propose_action" and res.get("card") and all(x["key"] != res["card"]["key"] for x in cards):
                    cards.append(res["card"])
                responses.append({"functionResponse": {"name": c["name"], "response": res}})
            contents.append({"role": "user", "parts": responses})
        else:
            # ran out of rounds while the model still wanted tools: ask for the answer without tools
            reply = llm.generate(system, contents + [{"role": "user", "parts": [{"text": "Answer now with the results you have."}]}],
                                 temperature=0.3)
        text = (reply.text if reply else "").strip()
        if not text:
            raise LLMError("empty answer")

        bad = guard.ungrounded(text, facts, message)
        if bad:
            # one repair round: tell the model which numbers it may not use
            contents.append({"role": "model", "parts": [{"text": text}]})
            contents.append({"role": "user", "parts": [{"text":
                "Rewrite your answer. These numbers did not come from any tool result: "
                + ", ".join(f"{b:g}" for b in bad[:8])
                + ". Use only numbers from the tool results (call `calculate` if you need arithmetic), or leave the number out."}]})
            r2 = llm.generate(system, contents, tools=DECLARATIONS, temperature=0.2)
            if r2.calls:  # it wants to compute: run those calls once, then ask again
                contents.append({"role": "model", "parts": r2.parts})
                resp = []
                for c in r2.calls:
                    res = run_tool(ctx, c["name"], c.get("args"))
                    facts.append(res)
                    calls_log.append({"name": c["name"], "args": c.get("args") or {}, "result": _short(res)})
                    resp.append({"functionResponse": {"name": c["name"], "response": res}})
                contents.append({"role": "user", "parts": resp})
                r2 = llm.generate(system, contents, temperature=0.2)
            text2 = (r2.text or "").strip()
            bad2 = guard.ungrounded(text2, facts, message) if text2 else bad
            if text2 and not bad2:
                text, bad = text2, []
            else:
                out = rules_reply(ctx, message, reason=f"AI answer had numbers not from the engine: {', '.join(f'{b:g}' for b in bad2[:5])}")
                out["tool_calls"] = calls_log
                return out

        if guard.claims_action(text):
            text += "\n\nNothing has changed: a price, stock order or mode changes only when you tap Yes."
        if not sources and not facts:
            sources = ["General guidance (no engine numbers used)"]
        intent = route(message)
        nx = ["profit", "returns", "raise"]
        if intent and p is not None:
            nx = rule_answer(intent, p, ctx.mode, ctx.products).get("next") or nx
        return {"answer": text, "engine": llm.name, "model": getattr(llm, "model", None), "intent": intent, "grounded": True,
                "sources": sources, "tool_calls": calls_log, "cards": cards,
                "next": nx, "chips": {k: CHIPS[k] for k in nx if k in CHIPS}, "rules": RULES, "fallback_reason": None}
    except LLMError as e:
        note_error("coach", e)
        log.warning("coach fell back to rules: %s", e)
        return rules_reply(ctx, message, reason=f"AI unavailable: {e}")
