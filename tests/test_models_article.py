from scraper.models.article import RawArticle


def test_raw_article_normalizes_and_hashes():
    a = RawArticle(
        url="https://example.com/path/?utm=1",
        outlet_id="x",
        outlet_name="X",
        title="T",
        source="rss",
    )
    assert "utm" not in a.url
    assert a.url_hash
