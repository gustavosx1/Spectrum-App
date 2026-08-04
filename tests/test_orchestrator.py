import asyncio

import pytest

from scraper.models.outlet import OutletConfig
from scraper.orchestrator import _collect_one


@pytest.mark.asyncio
async def test_collect_one_skips_playwright_for_folha_when_rss_is_empty(monkeypatch):
    outlet = OutletConfig(
        id="folha_sp",
        name="Folha de S.Paulo",
        base_url="https://www.folha.uol.com.br",
        political_score=62.0,
        rss_feeds=[],
        article_link_selector="a",
    )

    called = {"rss": 0, "play": 0}

    async def fake_collect_outlet_rss(_outlet, _client):
        called["rss"] += 1
        return []

    async def fake_scrape_outlet(_outlet):
        called["play"] += 1
        return [{"url": "https://www.folha.uol.com.br/poder/exemplo"}]

    monkeypatch.setattr("scraper.orchestrator.collect_outlet_rss", fake_collect_outlet_rss)
    monkeypatch.setattr("scraper.orchestrator.scrape_outlet", fake_scrape_outlet)

    result = await _collect_one(outlet, client=None, semaphore=asyncio.Semaphore(1))

    assert result == []
    assert called["rss"] == 0
    assert called["play"] == 0


@pytest.mark.asyncio
async def test_collect_one_still_uses_playwright_for_other_outlets(monkeypatch):
    outlet = OutletConfig(
        id="veja",
        name="Veja",
        base_url="https://veja.abril.com.br",
        political_score=72.0,
        rss_feeds=[],
        article_link_selector="a",
    )

    called = {"rss": 0, "play": 0}

    async def fake_collect_outlet_rss(_outlet, _client):
        called["rss"] += 1
        return []

    async def fake_scrape_outlet(_outlet):
        called["play"] += 1
        return [{"url": "https://veja.abril.com.br/politica/exemplo"}]

    monkeypatch.setattr("scraper.orchestrator.collect_outlet_rss", fake_collect_outlet_rss)
    monkeypatch.setattr("scraper.orchestrator.scrape_outlet", fake_scrape_outlet)

    result = await _collect_one(outlet, client=None, semaphore=asyncio.Semaphore(1))

    assert len(result) == 1
    assert called["play"] == 1
