"""Request dependencies: DB session, current seller, optional API key."""
from __future__ import annotations

from typing import Optional

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..models import Product, Seller
from ..services.seed import DEMO_SELLER_ID


def current_seller(db: Session = Depends(get_db), x_seller_id: Optional[str] = Header(default=None)) -> Seller:
    """The seller making the request. Demo: header X-Seller-Id, default 'demo'.
    Production: replace with Meesho supplier-panel login (OTP / SSO token)."""
    sid = x_seller_id or DEMO_SELLER_ID
    s = db.get(Seller, sid)
    if not s:
        raise HTTPException(404, f"seller {sid!r} not found")
    return s


def require_key(x_api_key: Optional[str] = Header(default=None)) -> None:
    """Write endpoints need X-API-Key when PP_API_KEY is set (off by default for the laptop demo)."""
    key = get_settings().api_key
    if key and x_api_key != key:
        raise HTTPException(401, "missing or wrong X-API-Key")


def own_product(product_id: str, db: Session, seller: Seller) -> Product:
    p = db.get(Product, product_id)
    if not p or p.seller_id != seller.id:
        raise HTTPException(404, f"product {product_id!r} not found")
    return p
