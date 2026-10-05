"""PLANNED: LightGBM demand model, monotone in price.

Not implemented yet: it needs real price-varied sales data, which the pilot
creates (menus rotate by day, so every listing is observed at 2–3 prices).

Plan:
  features  : ln(price), price ÷ look-alike median, category, listing age, CTR, rating,
              look-alike count, day of week, festival flags, delivery days
  target    : orders per 1,000 impressions (and kept orders per 1,000)
  constraint: monotone_constraints = −1 on price (higher price never predicts more orders)
  validation: time-based split; compare with the PriorDemand baseline on held-out weeks
  serving   : export to models/demand_lgbm.txt and register it in app/ml/registry.py
              behind the DemandModel interface (app/ml/interfaces.py)

Data schema: see data/sample/price_tests.csv (same columns plus the features above).
"""


def main() -> None:
    raise NotImplementedError("Needs price-varied sales data from the pilot. See the docstring for the plan.")


if __name__ == "__main__":
    main()
