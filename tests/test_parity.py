"""The Python engine must agree with the app's JavaScript engine to the rupee.

tests/golden_app.json was produced by running the app's own functions (recFor, FL, lcModel)
in a browser for 3 scenarios × 4 goal modes × 5 products, plus the lifecycle story.
"""
import json
from dataclasses import replace
from pathlib import Path

import pytest

from app.engine.catalog import demo_product
from app.engine.floor import floor_for
from app.engine.lifecycle import life_story
from app.engine.recommend import recommend

GOLDEN = json.loads((Path(__file__).parent / "golden_app.json").read_text(encoding="utf-8"))
OV_MAP = {"cs": "cs", "pack": "pack", "fwd": "fwd", "ret": "ret", "rto": "rto", "T": "target", "cat": "category"}


def product_for(row):
    p = demo_product(row["id"])
    kw = {OV_MAP[k]: v for k, v in (row["ov"] or {}).items()}
    p = replace(p, live_price=row["live"], days_since_move=row["dsm"], moves_this_month=row["mpm"], **kw)
    return p


@pytest.mark.parametrize("row", GOLDEN["scenarios"], ids=lambda r: f"{r['scenario']}-{r['mode']}-{r['id']}")
def test_recommendation_and_floor_match_app(row):
    p = product_for(row)
    f = floor_for(p)
    assert (f.F, f.start_price, f.no_return_price, f.gap, f.floor_no_return, f.safety_margin) == \
           (row["F"], row["Pe"], row["Pn"], row["gap"], row["Fno"], row["sm"])
    assert f.price_points == row["arms"]
    assert (f.clear_price, f.margin_price, f.recovery_floor) == (row["Fplus"], row["Pm"], row["frec"])
    assert f.k == pytest.approx(row["k"])
    r = recommend(p, row["mode"], f)
    assert r["key"] == row["key"]
    assert (r["kind"], r["from"], r["to"]) == (row["kind"], row["from"], row["to"])
    assert (None if r["pf"] is None else [c["ok"] for c in r["pf"]]) == row["pf"]


@pytest.mark.parametrize("sku", list(GOLDEN["lifecycle"]))
def test_life_story_matches_app(sku):
    g = GOLDEN["lifecycle"][sku]
    s = life_story(demo_product(sku))
    rt = s["rival_test"]
    assert (s["P0"], s["P1"], s["rival"], s["post_rival_price"]) == (g["P0"], g["P1"], g["Rv"], g["Pm"])
    assert (rt["decision"] == "match") == g["doMatch"]
    assert s["markdowns"] == g["M"]
    assert rt["orders_if_hold"] == g["holdO"] and rt["orders_if_match"] == pytest.approx(g["matchO"])
    assert rt["profit_day_hold"] == pytest.approx(g["hold"], abs=0.06)
    assert rt["profit_day_match"] == pytest.approx(g["match"], abs=0.06)
    assert [[d, pr] for d, pr, _ in s["events"]] == g["ev"]
    assert s["orders_per_day"] == [pytest.approx(x) for x in g["ordPts"]]
    assert s["windows"] == g["w"]
