"""AI API: Gemini-powered Coach, listing check, return-reason and review insights, plain-language Why.

Each endpoint works without a Gemini key too: it falls back to the rule-based / baseline path and says so
in `engine` ("rules") and `fallback_reason`.
"""
from __future__ import annotations

import base64
import binascii
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..ai import coach_agent, explain as explain_mod, insights, listing
from ..ai.provider import ai_status, allow
from ..ai.tools import ToolContext
from ..db import get_db
from ..engine.catalog import CATEGORIES
from ..models import CoachMessage, Seller
from ..services.core import to_engine
from .deps import current_seller, own_product

router = APIRouter()


def rate_limited(seller: Seller = Depends(current_seller)) -> Seller:
    if not allow(seller.id):
        raise HTTPException(429, "Too many AI requests; wait a minute.")
    return seller


class Turn(BaseModel):
    role: Literal["user", "model", "assistant", "coach"] = "user"
    text: str = Field("", max_length=4000)


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=1500)
    product_id: Optional[str] = None
    mode: Optional[Literal["cash", "growth", "margin", "clear"]] = None
    lang: Optional[Literal["en", "hi"]] = None
    history: List[Turn] = Field(default_factory=list, max_length=20)


class ListingIn(BaseModel):
    title: str = Field(min_length=3, max_length=300)
    description: str = Field("", max_length=5000)
    category: Optional[str] = None
    cost: Optional[float] = Field(None, gt=0, lt=100000, description="sourcing cost per piece, for the floor")
    target_profit: Optional[float] = Field(None, ge=0, lt=100000)
    lookalikes: Optional[int] = Field(None, ge=0)
    image_base64: Optional[str] = Field(None, description="product photo, base64 (data: URL prefix allowed), ≤ 4 MB")
    image_mime: Optional[str] = "image/jpeg"


class TextsIn(BaseModel):
    texts: List[str] = Field(min_length=1, max_length=300)
    product_id: Optional[str] = None


# ---------------------------------------------------------------- status
@router.get("/ai/status", tags=["ai"])
def status():
    """Which engine serves each AI feature (gemini or rules) and the last error per feature."""
    return ai_status()


# ---------------------------------------------------------------- coach
@router.post("/coach/chat", tags=["ai", "coach"])
def coach_chat(body: ChatIn, db: Session = Depends(get_db), seller: Seller = Depends(rate_limited)):
    """Ask anything in Hindi / Hinglish / English. Gemini calls the engine as tools; every number in the
    answer must come from a tool. Suggested moves come back as `cards` (Yes → POST /decisions)."""
    ctx = ToolContext.build(db, seller, body.product_id, body.mode)
    if not ctx.products:
        raise HTTPException(404, "no products yet")
    res = coach_agent.chat(ctx, body.message, [t.model_dump() for t in body.history], body.lang)
    db.add(CoachMessage(seller_id=seller.id, product_id=ctx.default_pid, text=body.message[:2000],
                        intent=(res.get("engine") or "rules")[:10] + ":" + (res.get("intent") or "-"), created_at=ctx.t))
    db.commit()
    res["product_id"] = ctx.default_pid
    return res


# ---------------------------------------------------------------- listing
def _image(b64: Optional[str], mime: Optional[str]) -> Optional[List[dict]]:
    if not b64:
        return None
    if b64.startswith("data:"):
        head, _, b64 = b64.partition(",")
        mime = head[5:].split(";")[0] or mime
    if mime not in ("image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"):
        raise HTTPException(422, "photo must be JPEG, PNG, WEBP or HEIC")
    if len(b64) > 5_600_000:
        raise HTTPException(413, "photo too large (max 4 MB)")
    try:
        base64.b64decode(b64[:1000] + "=" * (-len(b64[:1000]) % 4), validate=False)
    except (binascii.Error, ValueError):
        raise HTTPException(422, "photo is not valid base64")
    return [{"mime_type": mime, "data": b64}]


@router.post("/ai/listing/analyse", tags=["ai"])
def listing_analyse(body: ListingIn, seller: Seller = Depends(rate_limited)):
    """Category, attributes, craft score, listing score with fixes, India compliance flags, programme routing;
    with a cost, the floor and start price from the floor engine."""
    if body.category and body.category not in CATEGORIES:
        raise HTTPException(422, f"category must be one of {list(CATEGORIES)}")
    return listing.analyse(body.title, body.description, body.category, body.cost, body.target_profit, body.lookalikes,
                           _image(body.image_base64, body.image_mime))


# ---------------------------------------------------------------- returns + reviews
@router.post("/ai/returns/classify", tags=["ai"])
def returns_classify(body: TextsIn, db: Session = Depends(get_db), seller: Seller = Depends(rate_limited)):
    """Label free-text return reasons, count them, and say which share the seller can fix."""
    cost = None
    if body.product_id:
        p = own_product(body.product_id, db, seller)
        cost = CATEGORIES[p.category]["u_ret"]
    out = insights.classify_returns([t[:1000] for t in body.texts], cost)
    if cost:
        out["cost_per_return"] = cost
        out["cost_basis"] = "category cost of one return (reverse ship + handling)"
    return out


@router.post("/ai/reviews/insights", tags=["ai"])
def reviews_insights(body: TextsIn, seller: Seller = Depends(rate_limited)):
    """Topics, sentiment and an early-warning alert from buyer reviews (any Indian language)."""
    return insights.review_insights([t[:1000] for t in body.texts])


# ---------------------------------------------------------------- explain
@router.get("/products/{product_id}/explain", tags=["ai"])
def explain(product_id: str, mode: Optional[str] = None, lang: Literal["en", "hi", "hinglish"] = "en",
            db: Session = Depends(get_db), seller: Seller = Depends(rate_limited)):
    """This week's card for the product, with the Why rewritten in plain English / Hindi / Hinglish."""
    e = to_engine(db, own_product(product_id, db, seller))
    return explain_mod.explain(e, mode or seller.goal_mode, lang)
