# Playwright usado como fallback pra vaículos sem rss
from __future__ import annotations

import asyncio
import logging
import re
from typing import Optional
from urllib.parse import urlparse

from scraper.models.article import RawArticle, Source
from scraper.models.outlet import OutletConfig
from scraper.utils.text import (
    canonicalize_url,
    extract_author,
    extract_image,
    extract_lead,
    html_to_text,
    parse_date,
    is_within_window,
)
from scraper.config import settings

logger = logging.getLogger(__name__)


def _safe_filename_component(value: str) -> str:
    """Sanitiza um valor vindo do banco antes de usá-lo em nome de arquivo."""
    return re.sub(r"[^A-Za-z0-9_-]", "_", value)[:100]


def _is_same_site(url: str, outlet: OutletConfig) -> bool:
    """
    Restringe navegação/requisições a hosts do próprio outlet — evita que um
    link malicioso extraído da página (ex.: anúncio comprometido) ou um
    base_url adulterado no Supabase force o scraper a acessar hosts
    internos/arbitrários (SSRF).
    """
    try:
        target_host = (urlparse(url).hostname or "").lower()
        allowed_host = (urlparse(outlet.base_url).hostname or "").lower()
    except ValueError:
        return False
    if not target_host or not allowed_host:
        return False
    return target_host == allowed_host or target_host.endswith("." + allowed_host)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64; rv:124.0) Gecko/20100101 Firefox/124.0"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

"""Este PlayWright nunca foi utilizado, utilizamos somente RSS por enquanto.
 Mantemos o código para caso precisemos de scraping de páginas no futuro."""

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
        # Fallback leve: tenta buscar a página de índice via HTTP e extrair links
        logger.warning(
            "Playwright não instalado — usando fallback HTTP para coletar links (%s)",
            outlet.name,
        )
        try:
            import httpx
            from bs4 import BeautifulSoup

            target = outlet.url_scrape_target or outlet.base_url
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(target, headers=HEADERS, follow_redirects=True)
                resp.raise_for_status()
                soup = BeautifulSoup(resp.text, "lxml")
                els = soup.select(outlet.article_link_selector or "a")
                links = []
                from urllib.parse import urljoin

                for el in els:
                    href = el.get("href") or el.get("data-href") or el.get("data-url")
                    if not href:
                        a = el.find("a")
                        href = a.get("href") if a else None
                    if not href:
                        continue
                    resolved = urljoin(outlet.base_url, href.strip())
                    links.append(canonicalize_url(resolved))

                # Heurística adicional: se o selector não retornou links, varre todos
                # os <a> e filtra por padrões comuns de artigos (datas no path, tamanho).
                if not links:
                    import re

                    candidates = set()
                    for a in soup.find_all("a"):
                        href = a.get("href") or a.get("data-href") or a.get("data-url")
                        if not href:
                            continue
                        href = href.strip()
                        if href.startswith("#") or href.lower().startswith("javascript:"):
                            continue
                        # Resolve e normalize
                        resolved = urljoin(outlet.base_url, href)
                        # heuristics: contains year pattern (/2023/ /2024/ /2025/ /2026/)
                        if re.search(r"/20\d{2}/", resolved) or len(resolved) > 50:
                            candidates.add(canonicalize_url(resolved))

                    # fallback looser: include links that start with base_url
                    if not candidates:
                        for a in soup.find_all("a"):
                            href = a.get("href") or a.get("data-href") or a.get("data-url")
                            if not href:
                                continue
                            href = href.strip()
                            if href.startswith("#") or href.lower().startswith("javascript:"):
                                continue
                            resolved = urljoin(outlet.base_url, href)
                            if resolved.startswith(outlet.base_url) and len(resolved) > len(outlet.base_url) + 10:
                                candidates.add(canonicalize_url(resolved))

                    links = list(candidates)

                links = list(dict.fromkeys(links))
                links = [u for u in links if _is_same_site(u, outlet)]
                articles = []
                if not links:
                    logger.info("Fallback HTTP: nenhum link encontrado em %s", target)
                # Não renderizamos páginas — retornamos RawArticle mínimos para manter contrato
                for url in links[: outlet.max_articles_per_run]:
                    try:
                        ra = RawArticle(
                            url=url,
                            outlet_name=outlet.name,
                            outlet_id=outlet.id,
                            title=url,
                            source=Source.PLAY,
                        )
                        articles.append(ra.model_dump(mode="json"))
                    except Exception:
                        # Em caso de problema com validação, caímos para dict simples
                        articles.append({"url": url, "outlet_name": outlet.name, "outlet_id": outlet.id})
                return articles
        except Exception as e:
            logger.error("Fallback HTTP falhou para %s: %s", outlet.name, e)
            return []

    if not outlet.url_scrape_target and not outlet.base_url:
        logger.warning("Outlet %s sem configuração de scraping", outlet.name)
        return []
    if not outlet.article_link_selector:
        logger.warning("Outlet %s sem selector de links de artigos", outlet.name)
        return []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="pt-BR",
            extra_http_headers={
                "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )

        try:
            links = await _collect_article_links(context, outlet)
            articles = []
            for url in links[: outlet.max_articles_per_run]:
                article = await _scrape_article(context, url, outlet)
                if article:
                    articles.append(article)
                await asyncio.sleep(settings.request_delay_seconds)

            return articles
        finally:
            await browser.close()


async def _collect_article_links(context, outlet: OutletConfig) -> list[str]:
    page = await context.new_page()
    links: list[str] = []
    try:
        # Usa url_scrape_target quando configurado; fallback para base_url
        target = outlet.url_scrape_target or outlet.base_url
        await page.goto(
            target,
            wait_until="domcontentloaded",
            timeout=settings.playwright_page_timeout_ms,
        )
        # tenta aguardar networkidle também (páginas dinâmicas)
        try:
            await page.wait_for_load_state("networkidle", timeout=2000)
        except Exception:
            pass
        await page.wait_for_timeout(settings.playwright_wait_after_load_ms)

        # Salva HTML para depuração se o seletor não encontrar nada
        try:
            html_raw = await page.content()
            dbg_path = f"playwright_debug_{_safe_filename_component(outlet.id)}.html"
            with open(dbg_path, "w", encoding="utf8") as fh:
                fh.write(html_raw[:200000])
            logger.info("Saved page HTML for %s to %s", outlet.name, dbg_path)
        except Exception as e:
            logger.debug("Não foi possível salvar HTML de %s: %s", outlet.name, e)

        # O selector deve apontar para os elementos que contém o link
        # (normalmente <a> ou um container com data-href). Aceitamos
        # atributos comuns: href, data-href, data-url.
        from urllib.parse import urljoin

        sel_els = await page.query_selector_all(outlet.article_link_selector)
        logger.info("Playwright | %s | elements matching selector '%s': %d", outlet.name, outlet.article_link_selector, len(sel_els))
        for el in sel_els:
            href = None
            for attr in ("href", "data-href", "data-url", "data-link"):
                try:
                    href = await el.get_attribute(attr)
                except Exception:
                    href = None
                if href:
                    break
            if not href:
                # Tenta encontrar um <a> dentro do elemento
                try:
                    a = await el.query_selector("a")
                    if a:
                        href = await a.get_attribute("href")
                except Exception:
                    href = None
            if not href:
                continue
            href = href.strip()
            # Resolve URLs relativas com urljoin usando o base_url do outlet
            resolved = urljoin(outlet.base_url, href)
            links.append(canonicalize_url(resolved))

        # também loga número total de <a> como diagnóstico
        try:
            total_a = len(await page.query_selector_all("a"))
            logger.info("Playwright | %s | %d links encontrados (total <a>: %d)", outlet.name, len(links), total_a)
        except Exception:
            logger.info("Playwright | %s | %d links encontrados", outlet.name, len(links))
    except Exception as e:
        logger.warning("Erro ao coletar links de %s: %s", outlet.name, e)
    finally:
        await page.close()

    deduped = list(dict.fromkeys(links))  # dedup mantendo ordem
    return [u for u in deduped if _is_same_site(u, outlet)]


async def _scrape_article(context, url: str, outlet: OutletConfig) -> Optional[dict]:
    if not _is_same_site(url, outlet):
        logger.warning("Playwright | %s | URL fora do host do outlet, ignorando: %s", outlet.name, url)
        return None

    page = await context.new_page()
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=settings.playwright_page_timeout_ms)
        await page.wait_for_timeout(settings.playwright_wait_after_load_ms)

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

        # Filtra por janela de coleta (mesma regra do RSS)
        if not is_within_window(article.published_at):
            logger.debug("Playwright | %s | artigo fora da janela: %s", outlet.name, url)
            return None

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
