#!/usr/bin/env python3
"""Gera uma prévia somente-leitura da checagem inicial de tópicos.

Seleciona tópicos quentes cujos artigos não têm claims persistidas e reexecuta
a análise de checagem inicial em memória. O script nunca insere, atualiza ou
remove registros do Supabase.

Por segurança, chamadas Gemini só ocorrem com ``--execute``.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from worker.tasks import cluster
from worker.utils.db import get_client

DEFAULT_TOPIC_LIMIT = 20
DEFAULT_CANDIDATE_LIMIT = 100
TOPIC_COLUMNS = "id, canonical_title, article_count, is_hot, initial_check, created_at"
ARTICLE_COLUMNS = "id, url, title, lead, content, outlet_id, image_url, published_at"


def fetch_preview_topics(topic_limit: int, candidate_limit: int) -> list[dict[str, Any]]:
    """Lê tópicos aptos, omitindo aqueles com qualquer claim já persistida."""
    db = get_client()
    candidate_topics = (
        db.table("topics")
        .select(TOPIC_COLUMNS)
        .eq("is_hot", True)
        .order("created_at", desc=True)
        .limit(candidate_limit)
        .execute()
        .data
        or []
    )
    if not candidate_topics:
        return []

    topic_ids = [topic["id"] for topic in candidate_topics]
    articles = (
        db.table("articles")
        .select(ARTICLE_COLUMNS)
        .in_("topic_id", topic_ids)
        .order("published_at")
        .execute()
        .data
        or []
    )
    articles_by_topic: dict[str, list[dict[str, Any]]] = {topic_id: [] for topic_id in topic_ids}
    article_ids: list[str] = []
    article_topic_rows = (
        db.table("articles")
        .select("id, topic_id")
        .in_("topic_id", topic_ids)
        .execute()
        .data
        or []
    )
    topic_by_article_id = {
        row["id"]: row["topic_id"] for row in article_topic_rows if row.get("id") and row.get("topic_id")
    }
    for article in articles:
        topic_id = topic_by_article_id.get(article.get("id"))
        if topic_id:
            articles_by_topic[topic_id].append(article)
            article_ids.append(article["id"])

    claimed_article_ids: set[str] = set()
    if article_ids:
        claims = (
            db.table("claims").select("article_id").in_("article_id", article_ids).execute().data
            or []
        )
        claimed_article_ids = {claim["article_id"] for claim in claims if claim.get("article_id")}

    selected_topics: list[dict[str, Any]] = []
    for topic in candidate_topics:
        topic_articles = articles_by_topic[topic["id"]]
        if topic_articles and not any(article["id"] in claimed_article_ids for article in topic_articles):
            selected_topics.append({**topic, "articles": topic_articles})
        if len(selected_topics) == topic_limit:
            break
    return selected_topics


def _public_article(article: dict[str, Any], claims: list[dict[str, Any]] | None) -> dict[str, Any]:
    return {
        "article_id": article.get("id"),
        "url": article.get("url"),
        "title": article.get("title"),
        "lead": article.get("lead"),
        "published_at": article.get("published_at"),
        "claims": claims,
    }


async def analyze_topics(topics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Executa as mesmas funções de IA do worker, sem qualquer persistência."""
    results: list[dict[str, Any]] = []
    for number, topic in enumerate(topics, start=1):
        articles = topic["articles"]
        base = {
            "preview_topic": number,
            "topic_id": topic["id"],
            "current_canonical_title": topic.get("canonical_title"),
            "article_count": len(articles),
            "status": "analyzed",
        }
        try:
            editorial = await cluster._run_initial_prompt(articles)
            triage = await cluster._run_initial_triage(articles)
            claims_by_article_id = await cluster._build_claims_from_triage(articles, triage)
            base.update(
                {
                    "title": editorial["canonical_title"],
                    "summary": editorial["summary"],
                    "categories": editorial.get("categories", []),
                    "fact_check_status": cluster._fact_check_status_from_claims(
                        claims_by_article_id
                    ),
                    "triage": triage["verification"],
                    "articles": [
                        _public_article(article, claims_by_article_id.get(article["id"], []))
                        for article in articles
                    ],
                }
            )
        except Exception as error:  # Continua para permitir revisar os demais tópicos.
            base.update(
                {
                    "status": "analysis_error",
                    "error": str(error),
                    "articles": [_public_article(article, None) for article in articles],
                }
            )
        results.append(base)
    return results


def build_preview(topics: list[dict[str, Any]], requested_topics: int) -> dict[str, Any]:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "read_only_preview",
        "selection": {
            "requested_topic_count": requested_topics,
            "selected_topic_count": len(topics),
            "topic_filter": "is_hot = true",
            "claim_filter": "todos os artigos do tópico estão sem claims persistidas",
            "article_columns": ARTICLE_COLUMNS,
            "claims_sent_to_gemini": False,
        },
        "topics": topics,
    }


async def run_preview(args: argparse.Namespace) -> dict[str, Any]:
    topics = fetch_preview_topics(args.topics, args.candidate_limit)
    preview = build_preview(topics, args.topics)
    preview["topics"] = await analyze_topics(topics)
    return preview


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--topics", type=int, default=DEFAULT_TOPIC_LIMIT)
    parser.add_argument("--candidate-limit", type=int, default=DEFAULT_CANDIDATE_LIMIT)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("preview_fact_check.json"),
        help="Arquivo JSON local a ser gerado.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Confirma chamadas Gemini; sem esta flag não há custo nem análise.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not 1 <= args.topics <= 30:
        raise SystemExit("--topics deve estar entre 1 e 30")
    if not args.topics <= args.candidate_limit <= 500:
        raise SystemExit("--candidate-limit deve estar entre --topics e 500")
    if not args.execute:
        raise SystemExit(
            "Modo seguro: nenhuma chamada foi feita. Rode novamente com --execute "
            "para ler os artigos e chamar Gemini."
        )

    preview = asyncio.run(run_preview(args))
    args.output.write_text(
        json.dumps(preview, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    analyzed = sum(item["status"] == "analyzed" for item in preview["topics"])
    print(
        f"Prévia salva em {args.output} | {len(preview['topics'])} tópicos | "
        f"{analyzed} analisados"
    )


if __name__ == "__main__":
    main()
