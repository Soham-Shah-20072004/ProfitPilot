"""Category priors, goal modes, engine constants and the demo catalogue.

All numbers are ILLUSTRATIVE planning defaults from the team's deck, not real
Meesho data. They are the same numbers the demo app uses, so the app and the
API agree to the rupee.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional

# ---- engine constants (same names as in the app) ----
BETA = -3.0          # starting price sensitivity (category prior)
GAMMA = 120.0        # look-alike penalty strength above the market median
N0 = 2000            # prior strength of the bandit, in pseudo-impressions
CTR0 = 0.04          # click-through rate used by the bandit simulation
CVR0 = 0.12          # conversion rate used by the bandit simulation
DISPATCHED = 97      # of 100 placed orders, 3 are cancelled before dispatch
RETURN_PRIOR_STRENGTH = 58    # category return-rate prior is worth ~58 orders
RTO_PRIOR_STRENGTH = 245      # category RTO-rate prior is worth ~245 orders

# ---- guardrails (trigger hygiene) ----
MAX_STEP_PCT = 8.0
MIN_VIEWS = 1000
COOLDOWN_DAYS = 7
MAX_MOVES_PER_MONTH = 2
UNDO_WINDOW_HOURS = 24
JUDGE_DAY = 14
CONFIRM_DAY = 28

CATEGORIES: Dict[str, dict] = {
    "ethnic": {"name": "Ethnic wear", "u_ret": 226, "u_rto": 175,
               "other": {"dmg": 4, "promo": 6, "ads": 12, "tax": 10, "cap": 12},
               "ret": 12, "rto": 8, "life": "3–6 months", "beta": -3},
    "kitchen": {"name": "Home & kitchen", "u_ret": 240, "u_rto": 190,
                "other": {"dmg": 3, "promo": 7, "ads": 12, "tax": 12, "cap": 12},
                "ret": 5, "rto": 7, "life": "12+ months", "beta": -3},
    "beauty": {"name": "Beauty", "u_ret": 120, "u_rto": 110,
               "other": {"dmg": 2, "promo": 5, "ads": 9, "tax": 6, "cap": 6},
               "ret": 4, "rto": 9, "life": "~9 months (expiry)", "beta": -3},
    "kids": {"name": "Kids wear", "u_ret": 200, "u_rto": 160,
             "other": {"dmg": 3, "promo": 6, "ads": 12, "tax": 10, "cap": 12},
             "ret": 10, "rto": 8, "life": "4–6 months", "beta": -3},
    "decor": {"name": "Home décor", "u_ret": 230, "u_rto": 180,
              "other": {"dmg": 16, "promo": 6, "ads": 8, "tax": 7, "cap": 4},
              "ret": 9, "rto": 8, "life": "6–12 months", "beta": -3, "dmg_note": "+6% breakage"},
}

MODES: Dict[str, dict] = {
    "cash": {"name": "CASH", "definition": "Wants to reduce the wait to get cash from Meesho when products get sold.",
             "objective": "₹ profit per rupee-day (profit ÷ cash locked × days locked)"},
    "growth": {"name": "GROWTH", "definition": "OK to start with a small margin to get sales first, then increase slowly.",
               "objective": "₹ profit per impression, after a volume-first launch"},
    "margin": {"name": "MARGIN", "definition": "Strict about margin.",
               "objective": "₹ profit per impression, only arms with ≥ target profit per kept order"},
    "clear": {"name": "CLEAR", "definition": "Wants to sell fast; margin is not important, but never below the seller's cost.",
              "objective": "kept orders per impression (sell-through), never below floor F"},
}

STAGES = ["launch", "growth", "maturity", "decline", "exit"]


@dataclass
class Signals:
    """Weekly signals for one product (demo values; in production computed from metrics)."""
    stage: str = "launch"
    day: int = 0            # days since launch
    views: int = 0          # impressions at the current price
    cvr: float = 0          # conversion %, this product
    cvr_med: float = 0      # conversion %, similar products (median)
    cvr_weeks: int = 0      # weeks the conversion has held at or above the median
    doi: float = 0          # days of inventory
    rival: float = 0        # closest rival undercut in % (negative = rivals moved up)
    g: float = 0            # kept-unit trend: last 4 weeks vs the 4 weeks before, %
    ctr: float = 0          # click-through %
    kr: Optional[float] = None  # kept rate on matured orders, %


@dataclass
class Product:
    """Everything the engine needs to know about one listing."""
    id: str
    name: str
    emoji: str
    category: str
    offline_price: int      # the seller's offline / intended price
    ref_price: int          # price at which ref_orders was observed (demand anchor)
    ref_orders: float       # orders per day at ref_price, at full speed
    live_price: int         # price live right now
    cs: float               # sourcing cost per piece
    pack: float
    fwd: float
    ret: float              # return rate %, this product
    rto: float              # RTO rate %, this product
    target: float           # target profit per kept order (T)
    band: List[int]         # look-alike price band [p25, p75]
    median: int             # look-alike median
    lookalikes: int         # number of look-alike listings
    rival_price: int        # cheapest close rival
    recovery_floor: int     # variable-cost floor, Exit stage + consent only
    life_days: int          # expected life of the product
    signals: Signals = field(default_factory=Signals)
    days_since_move: int = 0
    moves_this_month: int = 0
    control: str = "cp"     # man | cp | au
    beta: float = BETA      # price sensitivity used by the demand model (−3 prior, or learned)

    def with_live(self, price: int) -> "Product":
        return replace(self, live_price=price)


# ---- demo catalogue (the five SKUs in the app) ----
DEMO_SKUS: Dict[str, dict] = {
    "kurti": dict(name="Printed Kurti", emoji="👗", category="ethnic", offline_price=399, ref_price=369, ref_orders=23,
                  cs=180, pack=10, fwd=25, ret=12, rto=8, target=60, band=[329, 429], median=379, lookalikes=24,
                  rival_price=299, recovery_floor=283, life_days=180, control="cp",
                  signals=dict(stage="growth", day=36, views=6200, cvr=13, cvr_med=12, cvr_weeks=2, doi=35, rival=2,
                               g=21, ctr=4.1, kr=79), days_since_move=36),
    "lunch": dict(name="Steel lunch box", emoji="🍱", category="kitchen", offline_price=449, ref_price=449, ref_orders=12,
                  cs=210, pack=15, fwd=45, ret=5, rto=7, target=63, band=[379, 499], median=439, lookalikes=31,
                  rival_price=329, recovery_floor=317, life_days=360, control="cp",
                  signals=dict(stage="maturity", day=140, views=4100, cvr=11, cvr_med=11, cvr_weeks=4, doi=8, rival=0,
                               g=3, ctr=3.9), days_since_move=60),
    "serum": dict(name="Vitamin-C serum", emoji="🧴", category="beauty", offline_price=249, ref_price=249, ref_orders=30,
                  cs=90, pack=8, fwd=22, ret=4, rto=9, target=33, band=[199, 299], median=259, lookalikes=40,
                  rival_price=159, recovery_floor=152, life_days=270, control="cp",
                  signals=dict(stage="maturity", day=110, views=5000, cvr=12, cvr_med=12, cvr_weeks=3, doi=40, rival=-12,
                               g=2, ctr=4.0), days_since_move=52),
    "romper": dict(name="Baby romper", emoji="👶", category="kids", offline_price=349, ref_price=349, ref_orders=18,
                   cs=150, pack=10, fwd=24, ret=10, rto=8, target=53, band=[299, 399], median=349, lookalikes=28,
                   rival_price=249, recovery_floor=244, life_days=180, control="man",
                   signals=dict(stage="growth", day=45, views=2600, cvr=14, cvr_med=13, cvr_weeks=2, doi=32, rival=1,
                                g=23, ctr=4.2, kr=81), days_since_move=45),
    "vase": dict(name="Ceramic vase", emoji="🏺", category="decor", offline_price=499, ref_price=499, ref_orders=5.5,
                 cs=220, pack=25, fwd=40, ret=9, rto=8, target=72, band=[449, 599], median=519, lookalikes=12,
                 rival_price=349, recovery_floor=260, life_days=300, control="cp",
                 signals=dict(stage="decline", day=200, views=1400, cvr=9, cvr_med=11, cvr_weeks=0, doi=73, rival=0,
                              g=-22, ctr=3.0), days_since_move=40),
}


def demo_product(sku_id: str) -> Product:
    d = dict(DEMO_SKUS[sku_id])
    sig = Signals(**d.pop("signals"))
    return Product(id=sku_id, live_price=d["ref_price"], signals=sig, **d)


def demo_products() -> Dict[str, Product]:
    return {k: demo_product(k) for k in DEMO_SKUS}
