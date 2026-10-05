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
        "ai": _ai_slots(),
    }


def _ai_slots() -> list:
    """LLM-served slots: Gemini when a key is set, else the rule / baseline backup named here."""
    from ..ai.provider import ai_status
    st = ai_status()
    llm = f"{st['provider']} · {st['model']}" if st["active"] else None
    return [
        {"slot": "coach", "active": llm or "rule-based Coach (keyword routing + engine answers)",
         "backup": "rule-based Coach", "note": "function-calling over 18 read-only engine tools; numbers grounded"},
        {"slot": "listing_check", "active": llm or "keyword baseline", "backup": "keyword baseline",
         "target": "CLIP image + text embeddings (planned); Gemini vision stands in until catalogue images are available"},
        {"slot": "return_reasons", "active": llm or "keyword baseline", "backup": "keyword baseline",
         "note": "AI labels; ProfitPilot counts"},
        {"slot": "review_intelligence", "active": llm or "keyword baseline", "backup": "keyword baseline",
         "target": "Indic-language topic model (2.0)"},
        {"slot": "why_explainer", "active": llm or "templates", "backup": "templates", "note": "0 ungrounded ₹ (guard)"},
    ]
