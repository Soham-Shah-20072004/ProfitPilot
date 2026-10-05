"""Number helpers that behave exactly like the JavaScript in the app.

The demo app (frontend/index.html) and this backend must produce the same
prices. JavaScript's Math.round rounds .5 up, while Python's round() rounds
.5 to the nearest even number, so every rounding in the engine goes through
js_round(). Formatting helpers produce the same text the app shows
(₹ with Indian digit grouping, signed percentages).
"""
from __future__ import annotations

import math
from decimal import ROUND_HALF_UP, Decimal


def js_round(x: float) -> int:
    """JavaScript Math.round: halves go up (2.5 -> 3, -2.5 -> -2)."""
    return int(math.floor(x + 0.5))


def ceil9(x: float) -> int:
    """Smallest price ending in 9 that is >= x (charm price)."""
    return int(math.ceil((x + 1) / 10) * 10 - 1)


def floor9(x: float) -> int:
    """Largest price ending in 9 that is <= x (charm price)."""
    return int(math.floor((x + 1) / 10) * 10 - 1)


def clamp(v: float, a: float, b: float) -> float:
    return max(a, min(b, v))


def to_fixed(x: float, d: int = 0) -> str:
    """JavaScript Number.prototype.toFixed (round half away from zero)."""
    q = Decimal(1).scaleb(-d)
    v = Decimal(abs(x)).quantize(q, rounding=ROUND_HALF_UP)
    s = format(v, "f")
    return ("-" + s) if (x < 0 and v != 0) else s


def group_in(n: int) -> str:
    """Indian digit grouping: 136800 -> '1,36,800'."""
    s = str(abs(int(n)))
    if len(s) > 3:
        last3, rest = s[-3:], s[:-3]
        parts = []
        while len(rest) > 2:
            parts.insert(0, rest[-2:])
            rest = rest[:-2]
        if rest:
            parts.insert(0, rest)
        s = ",".join(parts + [last3])
    return ("-" if n < 0 else "") + s


def inr(v: float) -> str:
    """₹ amount as the app shows it: '₹1,076', '−₹22'."""
    return ("−" if v < 0 else "") + "₹" + group_in(abs(js_round(v)))


def pct(v: float, d: int = 0) -> str:
    """Signed percentage as the app shows it: '+4%', '−6.5%'."""
    sign = "+" if v > 0 else ("−" if v < 0 else "")
    return sign + to_fixed(abs(v), d) + "%"
