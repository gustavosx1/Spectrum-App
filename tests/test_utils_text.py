import re
from datetime import datetime, timedelta, timezone

import pytest

from scraper.utils.text import (
    canonicalize_url,
    html_to_text,
    is_within_window,
    extract_author,
    extract_lead,
    extract_image,
    parse_date,
    truncate,
)


def test_canonicalize_url_removes_utm_and_trailing():
    url = "https://example.com/path/?utm_source=x&utm_medium=y&foo=bar#frag"
    clean = canonicalize_url(url)
    assert "utm_source" not in clean
    assert clean.endswith("/path") or clean.endswith("/path")


def test_html_to_text_strips_noise():
    html = "<html><head><script>bad</script></head><body><article><h1>Hi</h1><p>Para</p></article></body></html>"
    text = html_to_text(html)
    assert "Hi" in text
    assert "bad" not in text


def test_is_within_window_none_and_recent():
    assert is_within_window(None)
    now = datetime.now(tz=timezone.utc)
    assert is_within_window(now - timedelta(minutes=10))
    assert not is_within_window(now - timedelta(minutes=200), window_minutes=100)


def test_extract_author_variants():
    assert extract_author({"author": " Joao "}) == "Joao"
    assert extract_author({"authors": [{"name": " Maria "}]}) == "Maria"
    assert extract_author({}) is None


def test_extract_lead_from_description_and_content():
    entry = {"description": "<p>Lead aqui</p>", "content": []}
    assert extract_lead(entry).startswith("Lead")

    # fallback to content HTML
    entry = {"description": "", "content": [{"type": "text/html", "value": "<p>Par1</p><p>Par2</p>"}]}
    assert "Par1" in extract_lead(entry)


def test_extract_image_priorities():
    entry = {"media_content": [{"url": "https://img/1.jpg"}], "media_thumbnail": [], "enclosures": []}
    assert extract_image(entry) == "https://img/1.jpg"

    entry = {"media_content": [], "media_thumbnail": [{"url": "https://thumb/1.jpg"}], "enclosures": []}
    assert extract_image(entry) == "https://thumb/1.jpg"

    entry = {"media_content": [], "media_thumbnail": [], "enclosures": [{"type": "image/png", "href": "https://e/1.png"}]}
    assert extract_image(entry) == "https://e/1.png"


def test_parse_date_and_truncate():
    dt = parse_date("2020-01-01T12:00:00Z")
    assert dt is not None
    s = "a " * 50
    assert truncate(s, 10).endswith("…")
