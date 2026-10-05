"""Model registry: hands out the trained model if its artifact exists, else the baseline.

Artifacts live in models/ (see app/ml/training/README.md for how to make them):
  models/elasticity.json   -> learned β per category (train_elasticity.py)
  models/return_rates.json -> category return / RTO rates (train_return_risk.py)
"""
from __future__ import annotations

from ..config import ROOT
from .baseline import BetaBinomialReturnRisk, CatalogueLookalike, PriorDemand, load_json

MODELS_DIR = ROOT / "models"

PLANNED = {
    "demand": {"target": "LightGBM demand model, monotone in price, trained on price-varied orders",
               "needs": "orders, impressions and price per listing per day, with price variation (data/sample/price_tests.csv shows the schema)"},
    "return_risk": {"target": "per-pincode, COD vs prepaid return/RTO classifier",
                    "needs": "order-level outcomes with pincode, payment mode, size, reason codes"},
    "lookalike": {"target": "CLIP image + title embeddings with attribute filters",
                  "needs": "catalogue images, titles, attributes and live prices"},
}


def demand_model() -> PriorDemand:
    learned = load_json(MODELS_DIR / "elasticity.json")
    return PriorDemand(learned.get("categories") if learned else None)


def return_risk_model() -> BetaBinomialReturnRisk:
    rates = load_json(MODELS_DIR / "return_rates.json")
    return BetaBinomialReturnRisk(rates.get("categories") if rates else None)


def lookalike_model() -> CatalogueLookalike:
    return CatalogueLookalike()


def status() -> dict:
    el = (MODELS_DIR / "elasticity.json").exists()
    rr = (MODELS_DIR / "return_rates.json").exists()
    return {
        "models": [
            {"slot": "floor", "active": "deterministic floor engine", "trained": None, "note": "no ML needed"},
            {"slot": "demand", "active": demand_model().name, "trained_artifact": el, **PLANNED["demand"]},
            {"slot": "return_risk", "active": return_risk_model().name, "trained_artifact": rr, **PLANNED["return_risk"]},
            {"slot": "lookalike", "active": lookalike_model().name, "trained_artifact": False, **PLANNED["lookalike"]},
            {"slot": "price_search", "active": "constrained Thompson sampling, menus rotated by day", "trained": None,
             "note": "learns online from outcomes; no offline training"},
        ],
        "models_dir": str(MODELS_DIR),
    }
