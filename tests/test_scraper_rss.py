from scraper.collectors.scraper_rss import _resolve_outlet_for_entry
from scraper.models.outlet import OutletConfig


def _build_uol_outlet() -> OutletConfig:
    return OutletConfig(
        id="uol_noticias",
        name="UOL Noticias",
        base_url="https://noticias.uol.com.br",
        political_score=45.0,
        rss_feeds=["https://rss.uol.com.br/feed/noticias.xml"],
    )


def test_resolve_outlet_for_uol_routes_folha_domains_to_folha_sp():
    outlet = _build_uol_outlet()

    outlet_id, outlet_name = _resolve_outlet_for_entry(
        outlet,
        "https://www1.folha.uol.com.br/poder/2026/07/exemplo.shtml",
    )

    assert outlet_id == "folha_sp"
    assert outlet_name == "Folha de S.Paulo"


def test_resolve_outlet_for_uol_keeps_non_folha_domains_on_uol():
    outlet = _build_uol_outlet()

    outlet_id, outlet_name = _resolve_outlet_for_entry(
        outlet,
        "https://noticias.uol.com.br/politica/ultimas-noticias/2026/07/24/exemplo.htm",
    )

    assert outlet_id == "uol_noticias"
    assert outlet_name == "UOL Noticias"


def test_resolve_outlet_for_other_outlets_never_remaps():
    outlet = OutletConfig(
        id="g1",
        name="G1",
        base_url="https://g1.globo.com",
        political_score=48.0,
        rss_feeds=["https://g1.globo.com/rss/g1/"],
    )

    outlet_id, outlet_name = _resolve_outlet_for_entry(
        outlet,
        "https://www1.folha.uol.com.br/poder/2026/07/exemplo.shtml",
    )

    assert outlet_id == "g1"
    assert outlet_name == "G1"
