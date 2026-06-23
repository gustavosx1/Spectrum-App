# embedding para clusterização dos artigos em acontecimentos (tópicos)
from __future__ import annotations

import logging

import httpx

from worker.config import settings
import httpx

from google import genai

client = genai.Client(api_key=settings.gemini_api_key)
logger = logging.getLogger(__name__)

EMBEDDING_MODEL = "gemini-embedding-001"
EMBEDDING_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/{EMBEDDING_MODEL}:embedContent"
)


def build_embedding_input(title: str, lead: str):
    """Formatando o input pra dar mais peso ao título"""
    parts = [title, title]
    if lead:
        parts.append(lead)
        return ". ".join(parts)


async def generate_embedding(text: str) -> list[float]:
    result = client.models.embed_content(
        model=EMBEDDING_MODEL,
        contents=text,
    )

    return result.embeddings[0].values  # type: ignore[attr-defined]
