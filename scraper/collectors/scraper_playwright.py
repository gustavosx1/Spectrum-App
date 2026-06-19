# Playwright usado como fallback pra vaículos sem rss
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from scraper.models.article import RawArticle, Source
from scraper.models.outlet import OutletConfig
from scraper.utils.text import (
    canonicalize_url,
    extract_author,
    extract_image,
    extract_lead,
    html_to_text,
    parse_date,
)

logger = logging.getLogger(__name__)


async def _page_to_entry(page, html: str, outlet: OutletConfig) -> dict:
    """
    Monta um dict no mesmo formato de entry do feedparser a partir
    do HTML renderizado pelo Playwright + seletores do OutletConfig.
    Permite reutilizar extract_lead, extract_image e extract_author
    sem nenhuma lógica de extração duplicada.
    """
    og_image = _og_meta(html, "og:image")

    return {
        # Campos de texto — seletor do outlet tem prioridade, og como fallback
        "title": await _get_text(page, outlet.title_selector) or await page.title(),
        "description": await _get_text(page, outlet.lead_selector)
        or _og_meta(html, "og:description"),
        "summary": "",
        "author": await _get_text(page, outlet.author_selector),
        "published": (
            await _get_text(page, outlet.date_selector)
            or await _get_attr(page, "time[datetime]", "datetime")
        ),
        # Campos de mídia no formato que extract_image espera
        "content": [],
        "media_content": [{"url": og_image}] if og_image else [],
        "media_thumbnail": [],
        "enclosures": [],
    }


def _og_meta(html: str, property: str) -> Optional[str]:
    """Lê uma meta tag og: do HTML renderizado."""
    from bs4 import BeautifulSoup

    tag = BeautifulSoup(html, "lxml").find("meta", property=property)
    if tag and tag.get("content", "").strip():
        return tag["content"].strip()
    return None


async def scrape_outlet(outlet: OutletConfig) -> list[dict]:
    """
    Ponto de entrada do coletor Playwright.
    1. Abre a página de índice do veículo.
    2. Extrai links de artigos via selector configurado.
    3. Para cada link, extrai metadados + conteúdo.
    Retorna lista de dicts serializáveis, igual ao RSS.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logger.error(
            "Playwright não instalado. Execute: pip install playwright && python -m playwright install chromium"
        )
        return []

    if not outlet.url_scrape_target or not outlet.article_link_selector:
        logger.warning("Outlet %s sem configuração de scraping", outlet.name)
        return []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="pt-BR",
        )

        try:
            links = await _collect_article_links(context, outlet)
            articles = []
            for url in links[: outlet.max_articles_per_run]:
                article = await _scrape_article(context, url, outlet)
                if article:
                    articles.append(article)
                await asyncio.sleep(outlet.request_delay_seconds)

            return articles
        finally:
            await browser.close()


async def _collect_article_links(context, outlet: OutletConfig) -> list[str]:
    page = await context.new_page()
    links: list[str] = []
    try:
        await page.goto(
            outlet.url_scrape_target, wait_until="domcontentloaded", timeout=30000
        )
        await page.wait_for_timeout(2000)

        for el in await page.query_selector_all(outlet.article_link_selector):
            href = await el.get_attribute("href")
            if not href:
                continue
            if href.startswith("/"):
                href = outlet.base_url + href
            elif not href.startswith("http"):
                continue
            links.append(canonicalize_url(href))

        logger.info("Playwright | %s | %d links encontrados", outlet.name, len(links))
    except Exception as e:
        logger.warning("Erro ao coletar links de %s: %s", outlet.name, e)
    finally:
        await page.close()

    return list(dict.fromkeys(links))  # dedup mantendo ordem


async def _scrape_article(context, url: str, outlet: OutletConfig) -> Optional[dict]:
    page = await context.new_page()
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(1500)

        html = await page.content()
        entry = await _page_to_entry(page, html, outlet)

        article = RawArticle(
            url=url,
            outlet_name=outlet.name,
            outlet_id=outlet.id,
            title=entry["title"].strip(),
            lead=extract_lead(entry),
            content=html_to_text(html),
            author=extract_author(entry),
            image_url=extract_image(entry),
            published_at=parse_date(entry["published"]),
            source=Source.PLAY,
        )

        return article.model_dump(mode="json")

    except Exception as e:
        logger.warning("Erro ao raspar artigo %s: %s", url, e)
        return None
    finally:
        await page.close()


async def _get_text(page, selector: Optional[str]) -> Optional[str]:
    if not selector:
        return None
    try:
        el = await page.query_selector(selector)
        if el:
            return (await el.inner_text()).strip() or None
    except Exception:
        pass
    return None


async def _get_attr(page, selector: str, attr: str) -> Optional[str]:
    try:
        el = await page.query_selector(selector)
        if el:
            return await el.get_attribute(attr)
    except Exception:
        pass
    return None
