from __future__ import annotations

import hashlib
from datetime import datetime, UTC
from enum import Enum
from typing import Optional
from scraper.utils.text import canonicalize_url
from pydantic import BaseModel, Field, model_validator, field_validator


class ArticleStatus(str, Enum):
    RAW = "raw"  # artigo cru, após coleta
    EMBEDDED = "embedded"
    CLUSTERED = "clustered"
    VERIFIED = "verified"


class RawArticle(BaseModel):
    url: str
    url_hash: str = ""

    outlet_id: str
    outlet_name: str

    title: str
    author: Optional[str] = None
    lead: Optional[str] = None
    image_url: Optional[str] = None

    status: ArticleStatus = ArticleStatus.RAW

    published_at: Optional[datetime] = None
    collected_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("url", mode="before")
    @classmethod
    def normalize_url(cls, v):
        return canonicalize_url(v)

    @model_validator(mode="after")
    def generate_hash(self):
        if not self.url_hash:
            self.url_hash = hashlib.sha256(self.url.encode()).hexdigest()
        return self

    model_config = {"use_enum_values": True}
