"""Grounding guard: the LLM may explain, but every number it states must come from the engine.

check(answer, facts) pulls each number out of the answer (₹ amounts, percentages,
counts) and looks for it among the numbers in the tool results / facts the model
was given. Small numbers (≤ 12) and well-known guardrail constants are always
allowed. A number the engine never produced is "ungrounded".
"""
from __future__ import annotations

import json
import re
from typing import Any, Iterable, List, Set

# guardrail and calendar constants that may be quoted freely
ALLOWED = {14, 15, 18, 24, 28, 30, 60, 100, 1000, 300, 2025, 2026}
_NUM = re.compile(r"(?<![\w.])(?:₹\s?)?(-?\d{1,3}(?:,\d{2,3})+|-?\d+(?:\.\d+)?)\s*(%|k\b|K\b|L\b|lakh|cr\b|crore)?", re.I)
_DEV = str.maketrans("०१२३४५६७८९", "0123456789")


def _to_float(raw: str) -> float:
    return float(raw.replace(",", ""))


def numbers_in_text(text: str) -> List[float]:
    out = []
    for m in _NUM.finditer((text or "").translate(_DEV)):
        try:
            v = _to_float(m.group(1))
        except ValueError:
            continue
        suf = (m.group(2) or "").lower()
        if suf == "k":
            v *= 1000
        elif suf in ("l", "lakh"):
            v *= 100000
        elif suf in ("cr", "crore"):
            v *= 1e7
        out.append(v)
    return out


def _walk(x: Any, acc: List[float]) -> None:
    if isinstance(x, bool) or x is None:
        return
    if isinstance(x, (int, float)):
        acc.append(float(x))
    elif isinstance(x, str):
        acc.extend(numbers_in_text(x))
    elif isinstance(x, dict):
        for v in x.values():
            _walk(v, acc)
    elif isinstance(x, (list, tuple)):
        for v in x:
            _walk(v, acc)


def fact_numbers(facts: Iterable[Any]) -> Set[float]:
    raw: List[float] = []
    for f in facts:
        if isinstance(f, str):
            try:
                f = json.loads(f)
            except Exception:  # noqa: BLE001
                pass
        _walk(f, raw)
    out: Set[float] = set()
    for v in raw:
        for w in (v, abs(v)):
            out.add(round(w, 2))
            out.add(float(round(w)))
            out.add(round(w, 1))
            if abs(w) <= 1.5:                       # ratios: 0.78 ↔ 78%
                out.add(round(w * 100, 1))
                out.add(float(round(w * 100)))
    return out


def ungrounded(answer: str, facts: Iterable[Any], user_text: str = "") -> List[float]:
    known = fact_numbers(facts) | {round(v, 2) for v in numbers_in_text(user_text)} | {float(a) for a in ALLOWED}
    bad = []
    for v in numbers_in_text(answer):
        a = abs(v)
        if a <= 12:
            continue
        if any(abs(a - k) <= max(0.51, 0.005 * a) for k in known):
            continue
        bad.append(v)
    return bad


_ACTION_CLAIM = re.compile(
    r"\b(i|we)\s+(have\s+)?(changed|updated|set|lowered|raised|reduced|increased|applied|published|ordered)\s+(your|the)\s+(price|stock|mode|order)"
    r"|maine\s+(price|daam)\s+(badal|badha|ghata)", re.I)


def claims_action(answer: str) -> bool:
    """True if the answer says the Coach itself changed something (it never does: only the seller's tap does)."""
    return bool(_ACTION_CLAIM.search(answer or ""))
