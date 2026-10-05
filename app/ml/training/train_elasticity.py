"""Learn price sensitivity (β) per category from price-varied sales data.

Model:  ln(orders / impressions) = α_product + β · ln(price) + noise
(product fixed effects remove "this listing just sells better"; β is shared per category)

Input CSV columns: date, product_id, category, price, impressions, orders
Output: models/elasticity.json  -> {"categories": {cat: {"beta": β, "n": rows, "n0": 25}}}

The API shrinks the learned β toward the −3 prior with weight w = n / (n + n0),
so a category with little price variation keeps behaving like the prior.

Run:  python -m app.ml.training.train_elasticity data/sample/price_tests.csv
"""
from __future__ import annotations

import csv
import json
import math
import sys
from collections import defaultdict

from ...config import ROOT


def fit(rows):
    by_cat = defaultdict(list)
    for r in rows:
        imp, orders, price = float(r["impressions"]), float(r["orders"]), float(r["price"])
        if imp <= 0 or orders <= 0 or price <= 0:
            continue
        by_cat[r["category"]].append((r["product_id"], math.log(price), math.log(orders / imp)))
    out = {}
    for cat, obs in by_cat.items():
        # demean within product (fixed effects)
        by_p = defaultdict(list)
        for pid, x, y in obs:
            by_p[pid].append((x, y))
        sxx = sxy = 0.0
        n = 0
        for pts in by_p.values():
            mx = sum(x for x, _ in pts) / len(pts)
            my = sum(y for _, y in pts) / len(pts)
            for x, y in pts:
                sxx += (x - mx) ** 2
                sxy += (x - mx) * (y - my)
                n += 1
        if sxx <= 1e-9:
            continue      # no price variation: nothing to learn
        out[cat] = {"beta": round(sxy / sxx, 3), "n": n, "n0": 25}
    return out


def main(path: str) -> None:
    with open(path, newline="") as fh:
        rows = list(csv.DictReader(fh))
    cats = fit(rows)
    dest = ROOT / "models"
    dest.mkdir(exist_ok=True)
    (dest / "elasticity.json").write_text(json.dumps({"categories": cats, "source": path}, indent=2))
    for c, v in cats.items():
        print(f"{c:10s} beta = {v['beta']:+.2f} from {v['n']} rows")
    print(f"saved {dest / 'elasticity.json'}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "data" / "sample" / "price_tests.csv"))
