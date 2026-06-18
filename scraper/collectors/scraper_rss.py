from __future__ import annotations

import asyncio
import logging
from typing import Optional

import feedparser
import httpx
from bs4 import BeautifulSoup

from scraper.models.article import RawArticle
from scraper.models.outlet import OutletConfig
from scraper.utils.text import (
    canonicalize_url,
    extract_lead,
    extract_image,
    extract_author,
    parse_date,
    is_within_window,
)

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; SpectrumBot/1.0; +https://spectrum.app/bot)"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9",
}

# Janela de coleta — artigos mais antigos que isso são descartados.
COLLECTION_WINDOW_MINUTES: int = 75


async def fetch_rss_feed(
    feed_url: str,
    outlet: OutletConfig,
    client: httpx.AsyncClient,
    max_articles: int = 30,
) -> list[dict]:
    """
    Coleta artigos de um único feed RSS e retorna lista de dicts
    prontos para serialização JSON / enfileiramento no Celery.

    Não abre nenhuma conexão adicional — tudo extraído do próprio feed.
    O conteúdo completo do artigo será buscado pelo worker de processamento,
    que já tem acesso ao banco e pode evitar re-buscar URLs já indexadas.
    """
    logger.info("RSS | %s | %s", outlet.name, feed_url)

    try:
        resp = await client.get(feed_url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except httpx.HTTPError as e:
        logger.warning("RSS fetch falhou: %s — %s", feed_url, e)
        return []

    feed = feedparser.parse(resp.text)

    if feed.bozo and not feed.entries:
        logger.warning("Feed inválido ou vazio: %s", feed_url)
        return []

    articles: list[dict] = []
    skipped = 0

    for entry in feed.entries[:max_articles]:
        url = entry.get("link", "").strip()
        if not url:
            continue

        published_at = parse_date(entry.get("published") or entry.get("updated"))

        if not is_within_window(published_at):
            skipped += 1
            logger.debug(
                "Fora da janela (%s) — ignorando: %s",
                published_at.strftime("%d/%m %H:%M") if published_at else "sem data",
                url,
            )
            continue

        article = RawArticle(
            url=canonicalize_url(url),
            outlet_name=outlet.name,
            outlet_id=outlet.id,
            title=entry.get("title", "").strip(),
            lead=extract_lead(entry),
            image_url=extract_image(entry),
            author=extract_author(entry),
            published_at=published_at,
            source="rss",
        )

        articles.append(article.model_dump(mode="json"))

    if skipped:
        logger.info(
            "RSS | %s | %d artigo(s) fora da janela de %dmin — ignorados",
            outlet.name,
            skipped,
            COLLECTION_WINDOW_MINUTES,
        )

    return articles


async def collect_outlet_rss(
    outlet: OutletConfig,
    client: httpx.AsyncClient,
) -> list[dict]:
    """
    Coleta todos os feeds RSS de um veículo, deduplicando por URL.
    Retorna lista de dicts serializáveis — prontos para o Celery ou JSON.
    """
    seen_urls: set[str] = set()
    all_articles: list[dict] = []

    for feed_url in outlet.rss_feeds:
        for article in await fetch_rss_feed(feed_url, outlet, client):
            if article["url"] not in seen_urls:
                seen_urls.add(article["url"])
                all_articles.append(article)

    logger.info("RSS | %s | %d artigos coletados", outlet.name, len(all_articles))
    return all_articles
