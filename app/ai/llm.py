"""LLM clients behind one small interface.

GeminiLLM talks to the Gemini REST API (generateContent) with httpx: function
calling, JSON mode with a response schema, and image input. FakeLLM replays
scripted turns so the whole AI layer can be tested offline.

Contents use Gemini's own shape so the agent loop is provider-neutral:
    [{"role": "user", "parts": [{"text": "..."}]},
     {"role": "model", "parts": [{"functionCall": {...}, "thoughtSignature": "..."}]},
     {"role": "user", "parts": [{"functionResponse": {"name": ..., "response": {...}}}]}]
Model turns are echoed back exactly as received (thought signatures included),
which Gemini 3 models require during multi-step function calling.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import httpx

log = logging.getLogger("profitpilot.ai")


class LLMError(RuntimeError):
    """Any failure talking to the model (network, quota, safety block, bad JSON)."""


@dataclass
class LLMReply:
    parts: List[dict]                                  # raw model parts, to echo back into the conversation
    text: str = ""                                     # all text parts joined
    calls: List[dict] = field(default_factory=list)    # [{"name": ..., "args": {...}}]
    usage: Dict[str, Any] = field(default_factory=dict)
    finish_reason: Optional[str] = None

    def json(self) -> Any:
        t = self.text.strip()
        if t.startswith("```"):
            t = t.strip("`")
            t = t[t.find("\n") + 1:] if "\n" in t else t
        try:
            return json.loads(t)
        except Exception as e:  # noqa: BLE001
            raise LLMError(f"model did not return valid JSON: {e}") from e


class LLM:
    name = "llm"
    model = ""

    def generate(self, system: str, contents: List[dict], tools: Optional[List[dict]] = None,
                 schema: Optional[dict] = None, temperature: float = 0.3, max_tokens: int = 2048) -> LLMReply:
        raise NotImplementedError

    # convenience: one prompt (plus optional images) → parsed JSON that follows `schema`
    def json(self, system: str, prompt: str, schema: dict, images: Optional[List[dict]] = None,
             temperature: float = 0.2) -> Any:
        parts: List[dict] = [{"text": prompt}]
        for im in images or []:
            parts.append({"inlineData": {"mimeType": im["mime_type"], "data": im["data"]}})
        return self.generate(system, [{"role": "user", "parts": parts}], schema=schema, temperature=temperature).json()


def _parse(data: dict) -> LLMReply:
    cands = data.get("candidates") or []
    if not cands:
        fb = data.get("promptFeedback", {})
        raise LLMError(f"no candidates (blocked: {fb.get('blockReason', 'unknown')})")
    c = cands[0]
    parts = (c.get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    calls = [{"name": p["functionCall"]["name"], "args": p["functionCall"].get("args") or {}}
             for p in parts if "functionCall" in p]
    if not parts and c.get("finishReason") not in (None, "STOP"):
        raise LLMError(f"empty answer (finishReason {c.get('finishReason')})")
    return LLMReply(parts=parts, text=text, calls=calls, usage=data.get("usageMetadata", {}),
                    finish_reason=c.get("finishReason"))


class GeminiLLM(LLM):
    name = "gemini"

    def __init__(self, api_key: str, model: str = "gemini-2.5-flash",
                 base_url: str = "https://generativelanguage.googleapis.com/v1beta", timeout: float = 30.0,
                 transport: Optional[httpx.BaseTransport] = None, retries: int = 2, sleep: Callable[[float], None] = time.sleep):
        if not api_key:
            raise ValueError("Gemini API key is empty")
        self.model = model
        self._url = f"{base_url.rstrip('/')}/models/{model}:generateContent"
        self._client = httpx.Client(timeout=timeout, transport=transport,
                                    headers={"x-goog-api-key": api_key, "Content-Type": "application/json"})
        self._retries = retries
        self._sleep = sleep

    def build_payload(self, system: str, contents: List[dict], tools: Optional[List[dict]] = None,
                      schema: Optional[dict] = None, temperature: float = 0.3, max_tokens: int = 2048) -> dict:
        gen: Dict[str, Any] = {"temperature": temperature, "maxOutputTokens": max_tokens}
        if schema is not None:
            gen["responseMimeType"] = "application/json"
            gen["responseSchema"] = schema
        body: Dict[str, Any] = {"contents": contents, "generationConfig": gen}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        if tools:
            body["tools"] = [{"functionDeclarations": tools}]
            body["toolConfig"] = {"functionCallingConfig": {"mode": "AUTO"}}
        return body

    def generate(self, system, contents, tools=None, schema=None, temperature=0.3, max_tokens=2048) -> LLMReply:
        body = self.build_payload(system, contents, tools, schema, temperature, max_tokens)
        last = None
        for attempt in range(self._retries + 1):
            try:
                r = self._client.post(self._url, json=body)
            except httpx.HTTPError as e:
                last = LLMError(f"network error: {e}")
            else:
                if r.status_code == 200:
                    return _parse(r.json())
                msg = r.text[:300]
                try:
                    msg = r.json().get("error", {}).get("message", msg)
                except Exception:  # noqa: BLE001
                    pass
                last = LLMError(f"Gemini HTTP {r.status_code}: {msg}")
                if r.status_code not in (429, 500, 502, 503, 504):
                    break
            if attempt < self._retries:
                self._sleep(0.6 * (2 ** attempt))
        raise last or LLMError("unknown error")


class FakeLLM(LLM):
    """Scripted model for tests and offline demos.

    `script` is a list; each item is either an LLMReply-like dict
    ({"text": ...} or {"calls": [{"name":..., "args":...}]}) or a callable
    (system, contents, tools, schema) -> dict. When the script runs out, `default` is used.
    """
    name = "fake"
    model = "fake-1"

    def __init__(self, script: Optional[list] = None, default: Optional[Any] = None):
        self.script = list(script or [])
        self.default = default
        self.requests: List[dict] = []

    def generate(self, system, contents, tools=None, schema=None, temperature=0.3, max_tokens=2048) -> LLMReply:
        self.requests.append({"system": system, "contents": json.loads(json.dumps(contents)), "tools": tools, "schema": schema})
        item = self.script.pop(0) if self.script else self.default
        if item is None:
            raise LLMError("fake model has no scripted reply")
        if callable(item):
            item = item(system, contents, tools, schema)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, (dict, list)) and schema is not None and "text" not in (item if isinstance(item, dict) else {}):
            item = {"text": json.dumps(item)}
        calls = item.get("calls", [])
        parts = ([{"text": item["text"]}] if item.get("text") else []) + [{"functionCall": c} for c in calls]
        return LLMReply(parts=parts, text=item.get("text", ""), calls=[{"name": c["name"], "args": c.get("args", {})} for c in calls])
