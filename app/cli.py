"""Small command line for operators.

    python -m app.cli recs            # this week's card for every product
    python -m app.cli jobs            # run the checks that are due now
    python -m app.cli advance 14      # move the demo clock forward 14 days and run due checks
    python -m app.cli reset           # wipe demo state and reseed
"""
from __future__ import annotations

import sys

from sqlalchemy import select

from .db import SessionLocal, init_db
from .engine.recommend import recommend
from .models import Product, Seller
from .services.core import clock_offset, now, set_clock_offset, to_engine
from .services.jobs import run_due
from .services.seed import reset_demo, seed_demo


def main(argv=None) -> None:
    argv = argv or sys.argv[1:]
    cmd = argv[0] if argv else "recs"
    init_db()
    with SessionLocal() as db:
        seed_demo(db)
        if cmd == "reset":
            reset_demo(db)
            print("demo data reset")
        elif cmd == "jobs":
            for r in run_due(db):
                print(r)
        elif cmd == "advance":
            days = float(argv[1]) if len(argv) > 1 else 7
            set_clock_offset(db, clock_offset(db) + days)
            db.commit()
            print(f"clock now {now(db):%Y-%m-%d %H:%M} (+{clock_offset(db):g} days)")
            for r in run_due(db):
                print(r)
        elif cmd == "recs":
            for s in db.scalars(select(Seller)):
                print(f"{s.name} · goal mode {s.goal_mode}")
                for p in db.scalars(select(Product).where(Product.seller_id == s.id)):
                    r = recommend(to_engine(db, p), s.goal_mode)
                    print(f"  {p.emoji} {p.name:18s} {r['kind']:5s} {r['h']}")
        else:
            print(__doc__)


if __name__ == "__main__":
    main()
