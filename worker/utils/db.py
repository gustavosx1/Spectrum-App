from __future__ import annotations

from typing import Optional
from supabase import create_client, Client
from worker.config import settings
from scraper.models.outlet import OutletConfig


def get_client() -> Client:
    return create_client(settings.supabase_url, settings.supabase_key)


def getOutlets(outlet_ids: Optional[list[str]] = None) -> list[OutletConfig]:

    db = get_client()
    query = db.table("outlets").select("*")
    
    # Se outlet_ids fornecido e não vazio, filtra
    if outlet_ids:
        query = query.in_("id", outlet_ids)
    
    data = query.execute().data
    
    # Converte dicts do Supabase para OutletConfig
    outlets: list[OutletConfig] = []
    for row in data:
        # Garante que rss_feeds é uma lista
        rss_feeds = row.get("rss_feeds") or []
        if isinstance(rss_feeds, str):
            rss_feeds = [rss_feeds]
        
        outlet = OutletConfig(
            id=row["id"],
            name=row["name"],
            base_url=row["base_url"],
            political_score=row["political_score"],
            rss_feeds=rss_feeds,
            article_link_selector=row.get("article_link_selector"),
            title_selector=row.get("title_selector"),
            lead_selector=row.get("lead_selector"),
            author_selector=row.get("author_selector"),
            date_selector=row.get("date_selector"),
            url_scrape_target=row.get("url_scrape_target"),
            max_articles_per_run=row.get("max_articles_per_run", 30),
        )
        outlets.append(outlet)
    
    return outlets
