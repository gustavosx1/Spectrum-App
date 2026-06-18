# normalizador de html e URLs
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
import re
from urllib.parse import urlparse, urlunparse

from bs4 import BeautifulSoup
from dateutil import parser as dateparser

logger = logging.getLogger(__name__)


def canonicalize_url(url: str) -> str:
    """
    Normaliza url e retira parametros de tracking
    """
    TRACKING_PARAMS = {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "fbclid",
        "gclid",
        "yclid",
        "ref",
        "_ga",
        "mc_cid",
        "mc_eid",
    }
    parsed = urlparse(url)
    if parsed.query:
        params = [
            p
            for p in parsed.query.split("&")
            if p.split("=")[0].lower() not in TRACKING_PARAMS
        ]
        clean_query = "&".join(params)
    else:
        clean_query = ""

    return urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path.rstrip("/"),
            parsed.params,
            clean_query,
            "",  # remove fragment
        )
    )


_NOISE_TAGS = {
    "script",
    "style",
    "nav",
    "header",
    "footer",
    "aside",
    "figure",
    "figcaption",
    "form",
    "button",
    "iframe",
}


def html_to_text(html: str, max_chars: int = 8000) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(_NOISE_TAGS):
        tag.decompose()

    body = soup.find("article") or soup.body or soup.find("main") or soup
    text = body.get_text(separator="\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = text.strip()

    return text[:max_chars]


COLLECTION_WINDOW_MINUTES: int = 75


def is_within_window(
    published_at: Optional[datetime],
    window_minutes: int = COLLECTION_WINDOW_MINUTES,
) -> bool:
    """
    Retorna True se o artigo está dentro da janela de coleta.

    Regras:
    - Sem data → aceita (não há como saber; melhor processar do que perder).
    - Com data sem timezone → assume UTC para comparação conservadora.
    - Com data futura → aceita (pode ser erro de fuso no feed; não descartar).
    - Mais antigo que a janela → descarta.
    """
    if published_at is None:
        return True

    now = datetime.now(tz=timezone.utc)

    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)

    cutoff = now - timedelta(minutes=window_minutes)

    if published_at > now + timedelta(minutes=10):
        logger.debug("Data futura suspeita (%s) — aceitando assim mesmo", published_at)
        return True

    return published_at >= cutoff


def extract_author(entry: dict) -> Optional[str]:
    if entry.get("author"):
        return entry["author"].strip()
    authors = entry.get("authors", [])
    if authors:
        return authors[0].get("name", "").strip() or None
    return None


def extract_lead(entry: dict) -> Optional[str]:
    raw = (
        entry.get("media_description")
        or entry.get("description")
        or entry.get("summary")
        or ""
    )

    if raw.strip():
        text = BeautifulSoup(raw, "lxml").get_text(separator=" ", strip=True)
        return text[:400] or None

    # Fallback: alguns feeds deixam description vazio mas publicam o HTML
    # completo em content:encoded. Extrai os <p> até atingir 450 chars.
    html_content = ""
    for c in entry.get("content", []):
        if "html" in c.get("type", ""):
            html_content = c.get("value", "")
            break
    if not html_content:
        return None

    text = ""
    for p in BeautifulSoup(html_content, "lxml").find_all("p"):
        chunk = p.get_text(strip=True)
        if not chunk:
            continue
        text = (text + " " + chunk).strip()
        if len(text) >= 450:
            return text[:450]

    return text or None


def extract_image(entry: dict) -> Optional[str]:
    for media in entry.get("media_content", []):
        url = media.get("url", "")
        if url:
            return url

    for thumb in entry.get("media_thumbnail", []):
        url = thumb.get("url", "")
        if url:
            return url

    # enclosure (podcast/imagecanexa)
    for enc in entry.get("enclosures", []):
        if enc.get("type", "").startswith("image/"):
            return enc.get("href") or enc.get("url")

    return None


def parse_date(raw: Optional[str]) -> Optional[datetime]:
    """
    Tenta converter qualquer string de data para datetime UTC.
    """
    if not raw:
        return None
    try:
        return dateparser.parse(raw, fuzzy=True)
    except Exception:
        return None


def truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rsplit(" ", 1)[0] + "…"
