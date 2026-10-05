"""Which model serves the AI layer: Gemini when a key is set, else none (rules / baselines take over)."""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Optional

from ..config import get_settings
from .llm import LLM, GeminiLLM

_override: Optional[LLM] = None
_cached: Optional[LLM] = None
_cached_key: Optional[tuple] = None
_last_error: Dict[str, str] = {}
_lock = threading.Lock()


def set_llm(llm: Optional[LLM]) -> None:
    """Force a model (tests, demos). Pass None to go back to the configured one."""
    global _override
    _override = llm


def get_llm() -> Optional[LLM]:
    global _cached, _cached_key
    if _override is not None:
        return _override
    s = get_settings()
    if not s.ai_enabled or not s.gemini_api_key:
        return None
    key = (s.gemini_api_key, s.gemini_model, s.gemini_base_url, s.gemini_timeout_sec)
    with _lock:
        if _cached is None or _cached_key != key:
            _cached = GeminiLLM(s.gemini_api_key, s.gemini_model, s.gemini_base_url, s.gemini_timeout_sec)
            _cached_key = key
    return _cached


def note_error(feature: str, err: Exception) -> None:
    _last_error[feature] = f"{type(err).__name__}: {str(err)[:200]}"


def ai_status() -> dict:
    s = get_settings()
    llm = get_llm()
    reason = None
    if llm is None:
        reason = "AI switched off (PP_AI_ENABLED=false)" if not s.ai_enabled else "no GEMINI_API_KEY set"
    features = ["coach", "listing", "returns", "reviews", "explain"]
    return {
        "provider": llm.name if llm else "rules",
        "model": getattr(llm, "model", None) if llm else None,
        "active": llm is not None,
        "reason": reason,
        "features": {f: ("gemini" if llm else "rules / baseline") for f in features},
        "fallback": "Every AI feature has a rule-based or baseline-model backup; numbers always come from the engine.",
        "last_errors": dict(_last_error),
    }


# ---------------------------------------------------------------- per-seller rate limit
_hits: Dict[str, Deque[float]] = defaultdict(deque)


def allow(seller_id: str, per_min: Optional[int] = None) -> bool:
    limit = per_min if per_min is not None else get_settings().ai_rate_per_min
    if limit <= 0:
        return True
    now = time.monotonic()
    q = _hits[seller_id]
    with _lock:
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        return True


def reset_rate_limits() -> None:
    _hits.clear()
