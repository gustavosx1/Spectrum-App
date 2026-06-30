from __future__ import annotations

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


# ── Auth ─────────────────────────────────────────────────────────────────────


class UserProfile(BaseModel):
    id: str
    email: str
    is_premium: bool
    premium_expires_at: Optional[datetime] = None
    created_at: datetime


# ── Feed ─────────────────────────────────────────────────────────────────────


class OutletSummary(BaseModel):
    id: str
    name: str
    political_score: float


class ClaimResponse(BaseModel):
    id: str
    claim: str
    verdict: str  # 'true' | 'partial' | 'false' | 'unverifiable'
    confidence: float
    evidence: Optional[str] = None


class ArticleResponse(BaseModel):
    id: str
    url: str
    title: str
    lead: Optional[str] = None
    image_url: Optional[str] = None
    author: Optional[str] = None
    published_at: Optional[datetime] = None
    outlet: OutletSummary
    political_lean: str
    checked: bool
    claims: list[ClaimResponse] = Field(default_factory=list)


class BlindspotResponse(BaseModel):
    left_count: int
    center_count: int
    right_count: int
    dominant_side: Optional[str] = None  # lado com cobertura desproporcional
    description: Optional[str] = None  # ex: "Pouco coberto pela direita"


class TopicListItem(BaseModel):
    id: str
    canonical_title: str
    summary: Optional[str] = None
    article_count: int
    is_hot: bool
    initial_check: bool
    created_at: datetime
    blindspot: BlindspotResponse


class TopicDetail(TopicListItem):
    articles_left: list[ArticleResponse] = Field(default_factory=list)
    articles_center_left: list[ArticleResponse] = Field(default_factory=list)
    articles_center: list[ArticleResponse] = Field(default_factory=list)
    articles_center_right: list[ArticleResponse] = Field(default_factory=list)
    articles_right: list[ArticleResponse] = Field(default_factory=list)


class PaginationMeta(BaseModel):
    limit: int
    offset: int
    has_more: bool
    total: Optional[int] = None


class TopicListResponse(BaseModel):
    data: list[TopicListItem]
    meta: PaginationMeta


# ── Payments ─────────────────────────────────────────────────────────────────


class PurchaseVerifyRequest(BaseModel):
    """
    Enviado pelo mobile após compra nas lojas.
    O receipt_token é o que a loja devolve após a transação.
    """

    platform: str  # 'ios' | 'android'
    receipt_token: str  # purchase token (Android) ou receipt (iOS)
    product_id: str  # ex: 'spectrum_monthly' | 'spectrum_yearly'


class PurchaseVerifyResponse(BaseModel):
    is_valid: bool
    is_premium: bool
    expires_at: Optional[datetime] = None
    message: str


class SubscriptionStatus(BaseModel):
    is_premium: bool
    platform: Optional[str] = None  # onde comprou
    product_id: Optional[str] = None
    expires_at: Optional[datetime] = None
    auto_renews: Optional[bool] = None
