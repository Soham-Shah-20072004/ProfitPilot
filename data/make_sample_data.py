"""Generate SYNTHETIC sample data that shows the schemas the training scripts expect.

Not real Meesho data. Run:  python data/make_sample_data.py
Writes data/sample/price_tests.csv and data/sample/orders.csv (deterministic, seed 42).
"""
from __future__ import annotations

import csv
import math
import random
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.engine.catalog import DEMO_SKUS  # noqa: E402

OUT = ROOT / "data" / "sample"
TRUE_BETA = {"ethnic": -4.2, "kitchen": -2.6, "beauty": -3.4, "kids": -3.8, "decor": -2.2}   # made-up "truth"


def main() -> None:
    rng = random.Random(42)
    OUT.mkdir(parents=True, exist_ok=True)
    start = date(2026, 6, 1)
    with open(OUT / "price_tests.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["date", "product_id", "category", "price", "impressions", "orders"])
        for sku, d in DEMO_SKUS.items():
            base, q0 = d["ref_price"], d["ref_orders"]
            menu = [base, round(base * 1.08), round(base * 1.16)]           # menus rotate by day
            for i in range(60):
                price = menu[i % 3]
                imp = int(rng.gauss(4500, 400))
                lam = q0 * (price / base) ** TRUE_BETA[d["category"]] * imp / 4500
                orders = max(0, int(round(rng.gauss(lam, math.sqrt(max(lam, 1))))))
                w.writerow([(start + timedelta(days=i)).isoformat(), sku, d["category"], price, imp, orders])
    with open(OUT / "orders.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["order_id", "product_id", "category", "payment", "pincode", "outcome", "matured"])
        n = 0
        for sku, d in DEMO_SKUS.items():
            for _ in range(400):
                n += 1
                cod = rng.random() < 0.7
                rto_p = d["rto"] / 100 * (1.3 if cod else 0.4)
                ret_p = d["ret"] / 100
                u = rng.random()
                outcome = "rto" if u < rto_p else ("returned" if rng.random() < ret_p else "kept")
                w.writerow([f"o{n:05d}", sku, d["category"], "cod" if cod else "prepaid",
                            f"{rng.choice(['3950', '3600', '1100', '5600', '7000'])}{rng.randint(10, 99)}", outcome,
                            1 if rng.random() < 0.9 else 0])
    print(f"wrote {OUT / 'price_tests.csv'} and {OUT / 'orders.csv'} (synthetic)")


if __name__ == "__main__":
    main()
