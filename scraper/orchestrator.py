from __future__ import annotations
import asyncio
import logging
from typing import Optional

import httpx

from scraper.collectors.scraper_rss import collect_outlet_rss
from scraper.collectors.scraper_playwright import scrape_outlet
from scraper.models.outlet import OUTLETS, OutletConfig

"""
Orquestrador de coleta.

Estratégia:
  1. Para cada veículo, tenta RSS primeiro.
  2. Se o veículo não tiver RSS configurado OU o RSS retornar 0 artigos,
     cai para Playwright (se configurado).
  3. Todos os veículos rodam em paralelo (semáforo limita concorrência).
  4. Retorna lista deduplicada de dicts prontos para enfileirar no Celery.

Uso básico:
    from scraper.orchestrator import run_collection
    articles = await run_collection()

Filtrar veículos:
    articles = await run_collection(outlet_ids=["g1", "folha_sp"])
"""


logger = logging.getLogger(__name__)

MAX_CONCURRENT_OUTLETS = 5


async def _collect_one(
    outlet: OutletConfig,
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
) -> list[dict]:
    async with semaphore:
        articles: list[dict] = []

        if outlet.rss_feeds:
            articles = await collect_outlet_rss(outlet, client)

        if not articles and outlet.url_scrape_target:
            logger.info("RSS vazio para %s — usando Playwright", outlet.name)
            articles = await scrape_outlet(outlet)

        return articles


async def run_collection(
    outlet_ids: Optional[list[str]] = None,
    deduplicate: bool = True,
) -> list[dict]:
    """
    Executa a coleta completa.

    Args:
        outlet_ids:  IDs dos veículos a coletar. None = todos.
        deduplicate: Remove artigos com a mesma URL antes de retornar.

    Returns:
        Lista de dicts serializáveis ordenada por published_at desc.
    """
    targets = {
        k: v for k, v in OUTLETS.items() if outlet_ids is None or k in outlet_ids
    }

    if not targets:
        logger.warning("Nenhum veículo encontrado para os IDs informados.")
        return []

    semaphore = asyncio.Semaphore(MAX_CONCURRENT_OUTLETS)

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(30),
        follow_redirects=True,
        limits=httpx.Limits(max_connections=20),
    ) as client:
        results = await asyncio.gather(
            *[_collect_one(outlet, client, semaphore) for outlet in targets.values()],
            return_exceptions=True,
        )

    all_articles: list[dict] = []
    for outlet_id, result in zip(targets.keys(), results):
        if isinstance(result, Exception):
            logger.error("Falha ao coletar %s: %s", outlet_id, result)
        elif isinstance(result, list):
            all_articles.extend(result)
        else:
            logger.warning(
                "Alguma loucura aconteceu que não retornou erro nem lista: %s: %s",
                outlet_id,
                result,
            )

    if deduplicate:
        seen: set[str] = set()
        unique: list[dict] = []
        for a in all_articles:
            if a["url"] not in seen:
                seen.add(a["url"])
                unique.append(a)
        all_articles = unique

    all_articles.sort(
        key=lambda a: a["published_at"] or a["collected_at"],
        reverse=True,
    )
    logger.info(
        "Coleta concluída: %d artigos de %d veículos", len(all_articles), len(targets)
    )
    return all_articles
