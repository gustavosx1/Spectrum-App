from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class OutletConfig:
    """
    Configuração de um veículo de notícias.
    """

    id: str
    name: str
    base_url: str
    political_score: float  # 0 = extrema esquerda, 50 = centro, 100 = extrema direita
    rss_feeds: list[str] = field(default_factory=list)

    # Fallback Playwright
    url_scrape_target: Optional[str] = None  # URL do índice/homepage para scraping
    article_link_selector: Optional[str] = None  # CSS selector dos links de artigos
    title_selector: Optional[str] = None
    lead_selector: Optional[str] = None
    content_selector: Optional[str] = None
    author_selector: Optional[str] = None
    date_selector: Optional[str] = None

    # Throttling
    request_delay_seconds: float = 2.0
    max_articles_per_run: int = 30


# ---------------------------------------------------------------------------
# Catálogo inicial — scores baseados na metodologia do Manchetômetro (IESP/UERJ)
# Atualizar mensalmente conforme metodologia própria do Spectrum.
# ---------------------------------------------------------------------------
OUTLETS: dict[str, OutletConfig] = {
    # ── Esquerda ────────────────────────────────────────────────────────────
    "brasil_de_fato": OutletConfig(
        id="brasil_de_fato",
        name="Brasil de Fato",
        base_url="https://www.brasildefato.com.br",
        political_score=5.0,
        rss_feeds=[
            "https://www.brasildefato.com.br/rss",
        ],
    ),
    "the_intercept_br": OutletConfig(
        id="the_intercept_br",
        name="The Intercept Brasil",
        base_url="https://theintercept.com/brasil",
        political_score=15.0,
        rss_feeds=[
            "https://theintercept.com/brasil/feed/?rss",
        ],
    ),
     "revista_forum": OutletConfig(
        id="revista_forum",
        name="Revista Fórum",
        base_url="https://revistaforum.com.br",
        political_score=8.0,
        rss_feeds=[
            "https://revistaforum.com.br/feed/",
        ],
    ),
    "congresso_em_foco": OutletConfig(
        id="congresso_em_foco",
        name="Congresso em Foco",
        base_url="https://congressoemfoco.uol.com.br",
        political_score=20.0,
        rss_feeds=["https://congressoemfoco.uol.com.br/feed/"],
    ),
    "nexojornal": OutletConfig(
        id="nexojornal",
        name="Nexo Jornal",
        base_url="https://www.nexojornal.com.br",
        political_score=35.0,
        rss_feeds=["https://www.nexojornal.com.br/rss.xml"],
    ),

    "pragmatismo_politico": OutletConfig(
        id="pragmatismo_politico",
        name="Pragmatismo Político",
        base_url="https://pragmatismopolitico.com.br",
        political_score=15.0,
        rss_feeds=[
            "https://pragmatismopolitico.com.br/feed/",
        ],
    ),
    # ── Centro-esquerda ─────────────────────────────────────────────────────
    "agencia_brasil": OutletConfig(
        id="agencia_brasil",
        name="Agência Brasil",
        base_url="https://agenciabrasil.ebc.com.br",
        political_score=35.0,
        rss_feeds=[
            "https://agenciabrasil.ebc.com.br/rss/ultimasnoticias/feed.xml",
        ],
    ),
       "congresso_em_foco": OutletConfig(
        id="congresso_em_foco",
        name="Congresso em Foco",
        base_url="https://congressoemfoco.uol.com.br",
        political_score=20.0,
        rss_feeds=[
            "https://congressoemfoco.uol.com.br/feed/",
        ],
    ),

    # ── Centro ──────────────────────────────────────────────────────────────
    "g1": OutletConfig(
        id="g1",
        name="G1 / Globo",
        base_url="https://g1.globo.com",
        political_score=48.0,
        rss_feeds=[
            "https://g1.globo.com/rss/g1/",
            "https://g1.globo.com/rss/g1/politica/",
            "https://g1.globo.com/rss/g1/economia/",
        ],
    ),
    "uol_noticias": OutletConfig(
        id="uol_noticias",
        name="UOL Notícias",
        base_url="https://noticias.uol.com.br",
        political_score=45.0,
        rss_feeds=[
            "https://rss.uol.com.br/feed/noticias.xml",
        ],
    ),
    "metropoles": OutletConfig(
        id="metropoles",
        name="Metrópoles",
        base_url="https://www.metropoles.com",
        political_score=50.0,
        rss_feeds=[
            "https://www.metropoles.com/feed",
        ],
    ),
    # ── Centro-direita ──────────────────────────────────────────────────────
    "folha_sp": OutletConfig(
        id="folha_sp",
        name="Folha de S.Paulo",
        base_url="https://www.folha.uol.com.br",
        political_score=62.0,
        rss_feeds=[
            "https://feeds.folha.uol.com.br/emcimadahora/rss091.xml",
            "https://feeds.folha.uol.com.br/poder/rss091.xml",
        ],
    ),
    "o_globo": OutletConfig(
        id="o_globo",
        name="O Globo",
        base_url="https://oglobo.globo.com",
        political_score=60.0,
        rss_feeds=[
            "https://oglobo.globo.com/rss.xml",
        ],
    ),

    # ── Direita ─────────────────────────────────────────────────────────────
    "o_antagonista": OutletConfig(
        id="o_antagonista",
        name="O Antagonista",
        base_url="https://oantagonista.com.br",
        political_score=78.0,
        rss_feeds=[
            "https://oantagonista.com.br/feed/",
        ],
    ),

    "crusoe": OutletConfig(
        id="crusoe",
        name="Crusoé",
        base_url="https://crusoe.com.br",
        political_score=75.0,
        rss_feeds=[
            "https://crusoe.com.br/feed/",
        ],
    ),

    "revista_oeste": OutletConfig(
        id="revista_oeste",
        name="Revista Oeste",
        base_url="https://revistaoeste.com",
        political_score=85.0,
        rss_feeds=[
            "https://revistaoeste.com/feed/",
        ],
    ),

    "jovem_pan": OutletConfig(
        id="jovem_pan",
        name="Jovem Pan",
        base_url="https://jovempan.com.br",
        political_score=70.0,
        rss_feeds=[
            "https://jovempan.com.br/feed/",
        ],
    ),

    "gazeta_do_povo": OutletConfig(
        id="gazeta_do_povo",
        name="Gazeta do Povo",
        base_url="https://www.gazetadopovo.com.br",
        political_score=80.0,
        rss_feeds=[
            "https://www.gazetadopovo.com.br/feed/",
        ],
    ),
    "veja": OutletConfig(
        id="veja",
        name="Veja",
        base_url="https://veja.abril.com.br",
        political_score=72.0,
        rss_feeds=[
            "https://veja.abril.com.br/feed/",
        ],
    ),
    # Additional outlets with Manchetômetro ratings
    "cnn_brasil": OutletConfig(
        id="cnn_brasil",
        name="CNN Brasil",
        base_url="https://www.cnnbrasil.com.br",
        political_score=55.0,
        rss_feeds=["https://www.cnnbrasil.com.br/rss/"],
    ),
 
    "terra": OutletConfig(
        id="terra",
        name="Terra",
        base_url="https://www.terra.com.br",
        political_score=50.0,
        rss_feeds=["https://www.terra.com.br/rss.xml"],
    ),

 
    "poder360": OutletConfig(
        id="poder360",
        name="Poder360",
        base_url="https://www.poder360.com.br",
        political_score=60.0,
        rss_feeds=["https://www.poder360.com.br/feed/"],
    ),
    
   
 
}
 