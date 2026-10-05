"""Interfaces for the four models the pilot needs.

The engine talks to these interfaces only, so a trained model can replace a
baseline without touching the API or the business rules:

  1. Floor            -> deterministic (app/engine/floor.py); no ML needed.
  2. ReturnRiskModel  -> P(return), P(RTO) for a product (later: per pincode, COD vs prepaid).
  3. LookalikeModel   -> similar listings: price band, median, count.
  4. DemandModel      -> orders at a price; price sensitivity (elasticity).
  (+ price search: the constrained Thompson-sampling bandit in app/engine/bandit.py)
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..engine.catalog import Product


@runtime_checkable
class DemandModel(Protocol):
    name: str
    version: str

    def orders_per_day(self, p: Product, price: float) -> float: ...

    def elasticity(self, p: Product) -> float: ...


@runtime_checkable
class ReturnRiskModel(Protocol):
    name: str
    version: str

    def rates(self, p: Product, matured_orders: int = 0, returns: int = 0, rto: int = 0) -> dict: ...


@runtime_checkable
class LookalikeModel(Protocol):
    name: str
    version: str

    def similar(self, p: Product, k: int = 30) -> dict: ...
