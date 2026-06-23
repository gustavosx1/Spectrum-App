# embedding para clusterização dos artigos em acontecimentos (tópicos)
from __future__ import annotations

import logging

import httpx

from worker.config import settings
import httpx

logger = logging.getLogger(__name__)

EMBEDDING_MODEL = "models/text-embedding-004"
EMBEDDING_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/{EMBEDDING_MODEL}:embedContent"
)


def build_embedding_input(title: str, lead: str):
    """Formatando o input pra dar mais peso ao título"""
    parts = [title, title]
    if lead:
        parts.append(lead)
        return ". ".join(parts)


async def generate_embedding(text: str, article_id: str, api_key: str):
    resp = await httpx.AsyncClient().post(
        EMBEDDING_URL,
        headers={"Content-Type": "application/json"},
        params={"key": api_key},
        json={
            "model": EMBEDDING_MODEL,
            "content": {"parts": [{"text": text}]},
        },
    )
    if resp.status_code != 200:
        logger.error(
            "Gemini Embedding falhou: %s - %s ", resp.status_code, resp.text[:200]
        )
    values = resp.json()["embedding"]["values"]
    logger.debug(
        "Embedding do artigo: %s gerado: %d dimensões", article_id, len(values)
    )
    return values
