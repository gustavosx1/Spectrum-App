from scripts.preview_fact_check import _public_article, build_preview


def test_build_preview_describes_claim_free_initial_check_selection():
    preview = build_preview([], requested_topics=10)

    assert preview["mode"] == "read_only_preview"
    assert preview["selection"]["requested_topic_count"] == 10
    assert preview["selection"]["topic_filter"] == "is_hot = true"
    assert preview["selection"]["claims_sent_to_gemini"] is False


def test_public_article_keeps_the_claims_generated_in_memory():
    article = {
        "id": "article-1",
        "url": "https://example.com/article",
        "title": "Título",
        "lead": "Lead",
        "published_at": "2026-09-28T10:00:00Z",
    }
    claims = [{"claim": "Fato", "verdict": "unverifiable"}]

    result = _public_article(article, claims)

    assert result["article_id"] == "article-1"
    assert result["claims"] == claims
    assert "topic_id" not in result
