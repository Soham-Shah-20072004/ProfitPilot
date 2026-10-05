"""Price search: constrained Thompson sampling over price MENUS, rotated by day.

Each arm is a menu: an easy-returns price p and a no-return price p − gap.
The gap is about the return cost saved, so the margin is about the same
whichever price the buyer picks, and the reward per impression is
θ × (p − F), where θ = P(impression → kept order).

Rules that make it safe and fair:
  * arms below the floor F are removed before sampling (MARGIN mode also
    removes arms below the target profit);
  * one Thompson draw per DAY: that menu is live for every buyer all day, so
    everyone sees the same price at any moment (time-block rotation);
  * starting beliefs come from the pooled demand model (β = −3 plus a softer
    look-alike penalty); the listing's own outcomes sharpen them;
  * the lift is measured against holdout sellers (seller-level), never by
    showing different buyers different prices.

`simulate_days` runs the policy against a hidden "true" demand curve for the
demo. In production the platform calls `choose_today` each morning and posts
the day's impressions and kept orders with `record_outcome`.
"""
from __future__ import annotations

import math
import random
from dataclasses import asdict, dataclass, field
from typing import List, Optional

from .catalog import BETA, CTR0, CVR0, GAMMA, N0, Product
from .floor import FloorResult, floor_for

BATCHES_PER_DAY = 48
IMPRESSIONS_PER_BATCH = 95
HOLDOUT_PER_BATCH = 5
PRIOR_PENALTY_FACTOR = 0.5   # the pooled model knows look-alikes matter, but less sharply than reality


@dataclass
class Arm:
    p: int          # easy-returns price
    pn: int         # no-return price
    a: float        # Beta posterior: successes (+ prior)
    b: float        # Beta posterior: failures (+ prior)
    a0: float
    b0: float
    n: int = 0      # impressions served
    kept: int = 0   # kept orders observed
    th: float = 0   # hidden true kept-order rate (simulation only)
    days: int = 0   # days this menu was live


@dataclass
class Holdout:
    p: int
    n: int = 0
    kept: int = 0
    th: float = 0


@dataclass
class ExperimentState:
    product_id: str
    mode: str
    seed: int
    day: int = 0
    arms: List[Arm] = field(default_factory=list)
    hold: Optional[Holdout] = None
    last: Optional[int] = None          # price of the menu live on the last day
    history: List[int] = field(default_factory=list)   # menu (easy price) per day
    rng_state: Optional[list] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "ExperimentState":
        d = dict(d)
        d["arms"] = [Arm(**a) for a in d.get("arms", [])]
        d["hold"] = Holdout(**d["hold"]) if d.get("hold") else None
        return ExperimentState(**d)


def _pen(p: float, med: float, gamma: float) -> float:
    return math.exp(-gamma * ((p - med) / med) ** 2) if p > med else 1.0


def true_rate(p: Product, price: float, f: FloorResult) -> float:
    """Hidden 'true' kept-order rate per impression used by the demo simulation."""
    return CTR0 * CVR0 * math.pow(price / f.start_price, BETA) * _pen(price, p.median, GAMMA) * f.k


def prior_rate(p: Product, price: float, f: FloorResult) -> float:
    """Starting belief from the pooled model (softer look-alike penalty)."""
    return CTR0 * CVR0 * math.pow(price / f.start_price, BETA) * _pen(price, p.median, GAMMA * PRIOR_PENALTY_FACTOR) * f.k


def eligible(arm: Arm, f: FloorResult, mode: str) -> bool:
    if arm.p < f.F:
        return False
    if mode == "margin" and arm.p - f.F < f.T:
        return False
    return True


def objective(arm: Arm, f: FloorResult, mode: str) -> float:
    return 1.0 if mode == "clear" else float(arm.p - f.F)


def new_experiment(p: Product, mode: str = "growth", seed: int = 7, holdout_price: Optional[int] = None) -> ExperimentState:
    f = floor_for(p)
    arms = []
    for i, price in enumerate(f.price_points):
        if i == 1:      # the no-return price is half of a menu, not an arm of its own
            continue
        pr = prior_rate(p, price, f)
        arms.append(Arm(p=price, pn=price - f.gap, a=N0 * pr, b=N0 * (1 - pr), a0=N0 * pr, b0=N0 * (1 - pr),
                        th=true_rate(p, price, f)))
    hp = holdout_price if holdout_price is not None else p.offline_price
    rng = random.Random(seed)
    st = ExperimentState(product_id=p.id, mode=mode, seed=seed, arms=arms,
                         hold=Holdout(p=hp, th=true_rate(p, hp, f)))
    st.rng_state = _dump_rng(rng)
    return st


def _dump_rng(rng: random.Random) -> list:
    v, internal, gauss = rng.getstate()
    return [v, list(internal), gauss]


def _load_rng(state: list) -> random.Random:
    rng = random.Random()
    rng.setstate((state[0], tuple(state[1]), state[2]))
    return rng


def _draw(st: ExperimentState, f: FloorResult, rng: random.Random) -> Optional[Arm]:
    best, bv = None, -1.0
    for a in st.arms:
        if not eligible(a, f, st.mode):
            continue
        v = rng.betavariate(a.a, a.b) * objective(a, f, st.mode)
        if v > bv:
            best, bv = a, v
    return best


def choose_today(p: Product, st: ExperimentState) -> dict:
    """Production path: one Thompson draw decides today's menu for every buyer."""
    f = floor_for(p)
    rng = _load_rng(st.rng_state) if st.rng_state else random.Random(st.seed)
    arm = _draw(st, f, rng)
    st.rng_state = _dump_rng(rng)
    if arm is None:
        return {"menu": None, "reason": "no eligible arm (all below floor or target)"}
    return {"menu": {"easy_returns": arm.p, "no_return": arm.pn}, "day": st.day + 1,
            "rule": "one menu for every buyer today; menus rotate by day"}


def record_outcome(st: ExperimentState, price: int, impressions: int, kept: int) -> None:
    """Production path: update the posterior of the menu that was live."""
    for a in st.arms:
        if a.p == price:
            a.n += impressions
            a.kept += kept
            a.a += kept
            a.b += impressions - kept
            a.days += 1
            st.day += 1
            st.last = price
            st.history.append(price)
            return
    raise ValueError(f"no arm with price {price}")


def simulate_days(p: Product, st: ExperimentState, days: int) -> ExperimentState:
    """Demo path: run the policy against the hidden true demand for `days` days."""
    f = floor_for(p)
    rng = _load_rng(st.rng_state) if st.rng_state else random.Random(st.seed)
    for _ in range(days):
        best = _draw(st, f, rng)
        if best is None:
            break
        for _b in range(BATCHES_PER_DAY):
            k = sum(1 for _ in range(IMPRESSIONS_PER_BATCH) if rng.random() < best.th)
            best.n += IMPRESSIONS_PER_BATCH
            best.kept += k
            best.a += k
            best.b += IMPRESSIONS_PER_BATCH - k
            hk = sum(1 for _ in range(HOLDOUT_PER_BATCH) if rng.random() < st.hold.th)
            st.hold.n += HOLDOUT_PER_BATCH
            st.hold.kept += hk
        best.days += 1
        st.day += 1
        st.last = best.p
        st.history.append(best.p)
    st.rng_state = _dump_rng(rng)
    return st


def summary(p: Product, st: ExperimentState) -> dict:
    """What the Engine lab shows: pulls, posterior reward, lift vs holdout sellers."""
    f = floor_for(p)
    tot = sum(a.n for a in st.arms) or 1
    arms = []
    best, bv = None, -1.0
    for a in st.arms:
        el = eligible(a, f, st.mode)
        mean = a.a / (a.a + a.b)
        if el and mean * objective(a, f, st.mode) > bv:
            best, bv = a, mean * objective(a, f, st.mode)
        arms.append({"easy_returns": a.p, "no_return": a.pn, "eligible": el, "blocked_below_floor": a.p < f.F,
                     "impressions": a.n, "share": round(a.n / tot, 3), "days_live": a.days, "kept": a.kept,
                     "posterior_mean": mean, "reward_per_1k_impressions": round(mean * (a.p - f.F) * 1000, 1) if el else None,
                     "shrinkage_w": round(a.n / (a.n + N0), 2), "true_rate": a.th})
    bp = sum(a.kept * (a.p - f.F) for a in st.arms) / tot if tot > 1 else 0
    hp = st.hold.kept / st.hold.n * (st.hold.p - f.F) if st.hold and st.hold.n else 0
    bpT = sum(a.n * a.th * (a.p - f.F) for a in st.arms) / tot if tot > 1 else 0
    hpT = st.hold.th * (st.hold.p - f.F) if st.hold else 0
    return {"product_id": p.id, "mode": st.mode, "seed": st.seed, "day": st.day, "floor": f.F,
            "live_today": None if st.last is None else {"easy_returns": st.last, "no_return": st.last - f.gap},
            "best_menu": None if best is None else {"easy_returns": best.p, "no_return": best.pn},
            "arms": arms, "impressions": sum(a.n for a in st.arms), "kept_orders": sum(a.kept for a in st.arms),
            "profit_per_impression": {"bandit": round(bp, 4), "holdout_sellers": round(hp, 4),
                                      "observed_lift_pct": round((bp - hp) / hp * 100, 1) if hp > 0 else None,
                                      "expected_bandit": round(bpT, 4), "expected_holdout": round(hpT, 4),
                                      "expected_lift_pct": round((bpT - hpT) / hpT * 100, 1) if hpT > 0 else None},
            "days_per_menu": {str(a.p): a.days for a in st.arms},
            "rules": ["arms below the floor are removed before sampling", "one menu per day for every buyer",
                      "holdout = similar sellers without ProfitPilot (seller-level)"]}
