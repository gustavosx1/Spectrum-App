from __future__ import annotations


import asyncio
import logging

from worker.celery_app import app
from worker.config import settings
from worker.utils.db import get_client
from worker.utils.embedding import build_embedding_input, generate_embedding

logger = logging.getLogger(__name__)

api_key = settings.gemini_api_key


@app.task(
    bind=True,
    max_retries=3,
    defaut_retry_delay=60,
    name="woker.tasks.embed.process_articles",
)
def process_article(self, article: dict):
    try:
        asyncio.run(_process(article))
    except Exception as exc:
        logger.error("Falha ao porcessar artigo: %s: %s ", article.get("url"), exc)
        raise self.retry(exc=exc)


def _find_or_create_topic(db, embedding: list[float], title: str):
    result = db.rpc(
        "find_similar_topic",
        {
            "query_embedding": embedding,
            "similarity_threshold": settings.topic_similarity_threshold,
            "window_hours": settings.topic_window_hours,
        },
    ).execute()
    if result.data:
        topic_id = result.data[0]["id"]
        logger.debug("Tópico similar encontrado: %s", topic_id)
        return topic_id

    new_topic = (
        db.table("topics")
        .insert(
            {
                "canonical_title": title,
                "embedding": embedding,
            }
        )
        .execute()
    )

    topic_id = new_topic.data[0]["id"]
    logger.debug("Novo tópico criado: %s", new_topic.data[0]["canonical_title"])
    return topic_id


async def _process(article: dict) -> None:
    db = get_client()
    url = article["url"]

    # ── 1. Deduplicação ─────────────────────────────────────────────────────
    existing = db.table("articles").select("id").eq("url", url).limit(1).execute()
    if existing.data:
        logger.debug("Artigo já existe, ignorando: %s", url)
        return

    # ── 2. Gera embedding ────────────────────────────────────────────────────
    text = build_embedding_input(article["title"], article.get("lead", ""))
    embedding = await generate_embedding(text)

    # ── 3. Busca tópico similar ──────────────────────────────────────────────
    topic_id = _find_or_create_topic(db, embedding, article["title"])

    # ── 4. Insere artigo ─────────────────────────────────────────────────────
    db.table("articles").insert(
        {
            "url": url,
            "outlet_id": article.get("outlet_id"),
            "title": article["title"],
            "lead": article.get("lead"),
            "author": article.get("author"),
            "image_url": article.get("image_url"),
            "published_at": article.get("published_at"),
            "collected_at": article.get("collected_at"),
            "source": article.get("source", "rss"),
            "embedding": embedding,
            "topic_id": topic_id,
        }
    ).execute()

    logger.info("Artigo inserido: %s → tópico %s", url, topic_id)

    # ── 5. Verifica se tópico virou hot ──────────────────────────────────────
    # O trigger do banco já atualiza article_count e is_hot automaticamente.
    # Aqui só consultamos pra decidir se disparamos a task de cluster.
    topic = (
        db.table("topics")
        .select("is_hot, article_count")
        .eq("id", topic_id)
        .single()
        .execute()
    )

    if (
        topic
        and topic.data
        and topic.data["is_hot"]
        and topic.data["article_count"] == settings.hot_topic_threshold
    ):
        # article_count == threshold (não >) garante que dispara só uma vez
        logger.info("Tópico %s atingiu threshold — disparando cluster", topic_id)
        from worker.tasks.cluster import process_hot_topic

        process_hot_topic.delay(topic_id)
