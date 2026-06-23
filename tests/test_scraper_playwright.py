from scraper.collectors.scraper_playwright import _og_meta


def test_og_meta_finds_og_image():
    html = '<html><head><meta property="og:image" content="https://img.test/1.png"/></head><body></body></html>'
    assert _og_meta(html, "og:image") == "https://img.test/1.png"

def test_og_meta_missing_returns_none():
    html = '<html><head></head><body></body></html>'
    assert _og_meta(html, "og:image") is None
