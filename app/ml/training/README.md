# Training

| Script | Status | Input | Output |
|---|---|---|---|
| `train_elasticity.py` | runs today | `data/sample/price_tests.csv` (date, product_id, category, price, impressions, orders) | `models/elasticity.json`: β per category |
| `train_return_risk.py` | runs today | `data/sample/orders.csv` (order_id, product_id, category, payment, pincode, outcome, matured) | `models/return_rates.json`: return / RTO rate per category |
| `train_demand_lgbm.py` | planned | price-varied sales data from the pilot | LightGBM demand model, monotone in price |
| `train_lookalike_clip.py` | planned | catalogue images, titles, attributes | look-alike band per listing |

The sample CSVs are **synthetic** (made by `data/make_sample_data.py`), so the learned numbers mean nothing yet; they only prove the pipeline runs end to end.

```bash
python -m app.ml.training.train_elasticity
python -m app.ml.training.train_return_risk
PP_USE_LEARNED_MODELS=true python run.py      # demand model uses the learned β (shrunk toward −3)
```

How a learned β is used: `β_used = w · β_learned + (1 − w) · (−3)` with `w = n / (n + n₀)`, so a category with little price variation keeps behaving like the prior.
