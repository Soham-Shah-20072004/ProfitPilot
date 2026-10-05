"""Category return and RTO rates from matured order outcomes.

Input CSV columns: order_id, product_id, category, payment (cod|prepaid), pincode, outcome (kept|returned|rto), matured (1|0)
Only matured orders (past the return window) are counted, so late returns are not missed.
Output: models/return_rates.json -> {"categories": {cat: {"ret": %, "rto": %, "orders": n}}}

The API combines these with each product's own matured orders (Beta-binomial).
Next step (planned): a per-pincode, COD-vs-prepaid classifier on the same file.

Run:  python -m app.ml.training.train_return_risk data/sample/orders.csv
"""
from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict

from ...config import ROOT


def fit(rows):
    agg = defaultdict(lambda: {"n": 0, "delivered": 0, "returned": 0, "rto": 0})
    for r in rows:
        if str(r.get("matured", "1")) not in ("1", "true", "True"):
            continue
        a = agg[r["category"]]
        a["n"] += 1
        if r["outcome"] == "rto":
            a["rto"] += 1
        else:
            a["delivered"] += 1
            if r["outcome"] == "returned":
                a["returned"] += 1
    return {c: {"ret": round(a["returned"] / a["delivered"] * 100, 2) if a["delivered"] else None,
                "rto": round(a["rto"] / a["n"] * 100, 2) if a["n"] else None, "orders": a["n"]}
            for c, a in agg.items()}


def main(path: str) -> None:
    with open(path, newline="") as fh:
        rows = list(csv.DictReader(fh))
    cats = fit(rows)
    dest = ROOT / "models"
    dest.mkdir(exist_ok=True)
    (dest / "return_rates.json").write_text(json.dumps({"categories": cats, "source": path}, indent=2))
    for c, v in cats.items():
        print(f"{c:10s} returns {v['ret']}% of delivered · RTO {v['rto']}% of dispatched · {v['orders']} matured orders")
    print(f"saved {dest / 'return_rates.json'}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "data" / "sample" / "orders.csv"))
