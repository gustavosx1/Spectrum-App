from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request

from api.middleware.auth import get_user_id
from api.models.schemas import (
    ArticleResponse,
    BlindspotResponse,
    ClaimResponse,
    OutletSummary,
    TopicDetail,
    TopicListItem,
)
from worker.utils.db import get_client
from api.utils.premium import require_premium

router = APIRouter()
logger = logging.getLogger(__name__)


# Mapeamento de score político → lean
def _score_to_lean(score: float) -> str:
    if score <= 20:
        return "left"
    if score <= 40:
        return "center_left"
    if score <= 60:
        return "center"
    if score <= 75:
        return "center_right"
    return "right"


def _build_blindspot(articles: list[dict], outlets_map: dict) -> BlindspotResponse:
    counts = {"left": 0, "center_left": 0, "center": 0, "center_right": 0, "right": 0}
    for a in articles:
        outlet = outlets_map.get(a["outlet_id"])
        if outlet:
            lean = _score_to_lean(outlet["political_score"])
            counts[lean] += 1

    left_total = counts["left"] + counts["center_left"]
    right_total = counts["center_right"] + counts["right"]
    center = counts["center"]
    total = left_total + center + right_total

    dominant = None
    description = None
    if total >= 3:
        if left_total == 0:
            dominant, description = "left", "Sem cobertura da esquerda"
        elif right_total == 0:
            dominant, description = "right", "Sem cobertura da direita"
        elif right_total > 0 and left_total / max(right_total, 1) >= 3:
            dominant, description = "right", "Pouco coberto pela direita"
        elif left_total > 0 and right_total / max(left_total, 1) >= 3:
            dominant, description = "left", "Pouco coberto pela esquerda"

    return BlindspotResponse(
        left_count=left_total,
        center_count=center,
        right_count=right_total,
        dominant_side=dominant,
        description=description,
    )


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.get("/topics", response_model=list[TopicListItem])
def list_topics(
    request: Request,
    limit: int = Query(default=20, le=50),
    offset: int = Query(default=0),
    only_hot: bool = Query(default=False),
):
    """
    Lista tópicos ordenados por mais recentes.
    Requer assinatura ativa.
    """
    db = get_client()
    _require_premium(request, db)

    # Busca tópicos
    query = (
        db.table("topics")
        .select(
            "id, canonical_title, summary, article_count, is_hot, initial_check, created_at"
        )
        .order("created_at", desc=True)
        .range(offset, offset + limit - 1)
    )
    if only_hot:
        query = query.eq("is_hot", True)

    topics = query.execute().data

    if not topics:
        return []

    topic_ids = [t["id"] for t in topics]

    # Busca artigos dos tópicos pra calcular blindspots
    articles = (
        db.table("articles")
        .select("topic_id, outlet_id")
        .in_("topic_id", topic_ids)
        .execute()
    ).data

    # Busca outlets pra mapear scores
    outlet_ids = list({a["outlet_id"] for a in articles if a["outlet_id"]})
    outlets_map = {}
    if outlet_ids:
        outlets = (
            db.table("outlets")
            .select("id, political_score")
            .in_("id", outlet_ids)
            .execute()
        ).data
        outlets_map = {o["id"]: o for o in outlets}

    # Agrupa artigos por tópico
    articles_by_topic: dict[str, list] = {t["id"]: [] for t in topics}
    for a in articles:
        if a["topic_id"] in articles_by_topic:
            articles_by_topic[a["topic_id"]].append(a)

    return [
        TopicListItem(
            **t,
            blindspot=_build_blindspot(articles_by_topic[t["id"]], outlets_map),
        )
        for t in topics
    ]


@router.get("/topics/{topic_id}", response_model=TopicDetail)
def get_topic(topic_id: str, request: Request):
    """
    Detalhe de um tópico com artigos agrupados por espectro e claims.
    Requer assinatura ativa.
    """
    db = get_client()
    require_premium(request)

    # Tópico
    topic = (
        db.table("topics")
        .select(
            "id, canonical_title, summary, article_count, is_hot, initial_check, created_at"
        )
        .eq("id", topic_id)
        .single()
        .execute()
    ).data

    if not topic:
        raise HTTPException(status_code=404, detail="Tópico não encontrado")

    # Artigos com outlet
    articles_raw = (
        db.table("articles")
        .select(
            "id, url, title, lead, image_url, author, published_at, outlet_id, political_lean, checked"
        )
        .eq("topic_id", topic_id)
        .order("published_at", desc=True)
        .execute()
    ).data

    # Outlets
    outlet_ids = list({a["outlet_id"] for a in articles_raw if a["outlet_id"]})
    outlets_map = {}
    if outlet_ids:
        outlets = (
            db.table("outlets")
            .select("id, name, political_score")
            .in_("id", outlet_ids)
            .execute()
        ).data
        outlets_map = {o["id"]: o for o in outlets}

    # Claims por artigo
    article_ids = [a["id"] for a in articles_raw]
    claims_map: dict[str, list] = {a["id"]: [] for a in articles_raw}
    if article_ids:
        claims = (
            db.table("claims")
            .select("id, article_id, claim, verdict, confidence, evidence")
            .in_("article_id", article_ids)
            .execute()
        ).data
        for c in claims:
            if c["article_id"] in claims_map:
                claims_map[c["article_id"]].append(
                    ClaimResponse(
                        id=c["id"],
                        claim=c["claim"],
                        verdict=c["verdict"],
                        confidence=c["confidence"] or 0.0,
                        evidence=c.get("evidence"),
                    )
                )

    # Monta ArticleResponse e agrupa por espectro
    grouped: dict[str, list[ArticleResponse]] = {
        "left": [],
        "center_left": [],
        "center": [],
        "center_right": [],
        "right": [],
    }

    for a in articles_raw:
        outlet = outlets_map.get(a["outlet_id"])
        if not outlet:
            continue

        lean = _score_to_lean(outlet["political_score"])
        grouped[lean].append(
            ArticleResponse(
                id=a["id"],
                url=a["url"],
                title=a["title"],
                lead=a.get("lead"),
                image_url=a.get("image_url"),
                author=a.get("author"),
                published_at=a.get("published_at"),
                outlet=OutletSummary(
                    id=outlet["id"],
                    name=outlet["name"],
                    political_score=outlet["political_score"],
                ),
                political_lean=lean,
                checked=a["checked"],
                claims=claims_map[a["id"]],
            )
        )

    blindspot = _build_blindspot(articles_raw, outlets_map)

    return TopicDetail(
        **topic,
        blindspot=blindspot,
        articles_left=grouped["left"],
        articles_center_left=grouped["center_left"],
        articles_center=grouped["center"],
        articles_center_right=grouped["center_right"],
        articles_right=grouped["right"],
    )
