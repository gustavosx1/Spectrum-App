import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from worker.tasks import cluster


class DummyResult:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, data, calls):
        self.data = data
        self.calls = calls
        self._single = False

    def select(self, *args, **kwargs):
        self.calls.append(("select", args, kwargs))
        return self

    def eq(self, *args, **kwargs):
        self.calls.append(("eq", args, kwargs))
        return self

    def gte(self, *args, **kwargs):
        self.calls.append(("gte", args, kwargs))
        return self

    def in_(self, *args, **kwargs):
        self.calls.append(("in_", args, kwargs))
        return self

    def order(self, *args, **kwargs):
        self.calls.append(("order", args, kwargs))
        return self

    def single(self):
        self.calls.append(("single", (), {}))
        self._single = True
        return self

    def limit(self, n):
        self.calls.append(("limit", (n,), {}))
        return self

    def execute(self):
        self.calls.append(("execute", (), {}))
        if self._single and isinstance(self.data, list):
            return DummyResult(self.data[0] if self.data else None)
        return DummyResult(self.data)

    def update(self, payload):
        self.calls.append(("update", payload))
        return self

    def insert(self, payload):
        self.calls.append(("insert", payload))
        return self

    def upsert(self, payload, **kwargs):
        self.calls.append(("upsert", payload, kwargs))
        return self


class FakeDB:
    def __init__(self, table_data=None):
        self.table_data = table_data or {}
        self.calls = []

    def table(self, name):
        self.calls.append(("table", name))
        data = self.table_data.get(name, [])
        return FakeQuery(data, self.calls)


class FakeResponse:
    def __init__(self, text=None, data=None):
        self.text = text
        self._data = data

    def raise_for_status(self):
        return None

    def json(self):
        return self._data


class FakeAsyncClient:
    def __init__(self, *args, **kwargs):
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, headers=None):
        self.calls.append(("get", url, headers))
        return FakeResponse(text="<html><body><p>conteúdo</p></body></html>")

    async def post(self, url, params=None, json=None, headers=None):
        self.calls.append(("post", url, params, json))
        return FakeResponse(
            data={
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "text": "```json\n{\"foo\": \"bar\"}\n```"
                                }
                            ]
                        }
                    }
                ]
            }
        )


class FakeExpoAsyncClient:
    def __init__(self, *args, **kwargs):
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, params=None, json=None, headers=None):
        self.calls.append(("post", url, params, json, headers))
        return FakeResponse(
            data={
                "data": [
                    {"status": "ok", "id": "ticket-1"},
                    {
                        "status": "error",
                        "message": "The device is not registered.",
                        "details": {"error": "DeviceNotRegistered"},
                    },
                ]
            }
        )


@pytest.mark.asyncio
async def test_fetch_articles_adds_checked_filter():
    articles = [
        {
            "id": "article-1",
            "url": "https://example.com/1",
            "title": "Test",
            "lead": "Lead",
            "content": None,
            "outlet_id": "o1",
        }
    ]
    db = FakeDB({"articles": articles})

    result = cluster._fetch_articles(db, "topic-1", only_unchecked=True)

    assert result == articles
    assert ("table", "articles") in db.calls
    assert ("eq", ("topic_id", "topic-1"), {}) in db.calls
    assert ("eq", ("checked", False), {}) in db.calls


@pytest.mark.asyncio
async def test_fetch_contents_updates_articles(monkeypatch):
    db = FakeDB()
    articles = [{"id": "article-1", "url": "https://example.com/1", "content": None}]

    monkeypatch.setattr(cluster.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr("scraper.utils.text.html_to_text", lambda html: "texto limpo")

    await cluster._fetch_contents(db, articles)

    assert ("table", "articles") in db.calls
    assert any(call[0] == "update" and call[1] == {"content": "texto limpo"} for call in db.calls)


@pytest.mark.asyncio
async def test_run_initial_prompt_builds_expected_prompt(monkeypatch):
    captured = {}

    async def fake_call(prompt, **kwargs):
        captured["prompt"] = prompt
        captured["kwargs"] = kwargs
        return {
            "canonical_title": "Título neutro",
            "summary": "Resumo dos fatos.",
            "articles": [
                {"article_id": "article-1", "claims": []}
            ],
        }

    monkeypatch.setattr(cluster, "_call_gemini", fake_call)

    articles = [
        {
            "id": "article-1",
            "url": "https://example.com/1",
            "title": "Título original",
            "lead": "Lead do artigo",
            "content": "Conteúdo completo do artigo.",
        }
    ]

    result = await cluster._run_initial_prompt(articles)

    assert result["canonical_title"] == "Título neutro"
    assert "Título original" in captured["prompt"]
    assert "Lead do artigo" in captured["prompt"]
    assert "Conteúdo completo" in captured["prompt"]
    assert "Preserve a linha do tempo" in captured["prompt"]
    assert "máx 80 caracteres" in captured["prompt"]
    assert captured["kwargs"]["purpose"] == "editorial_initial"


@pytest.mark.asyncio
async def test_call_gemini_parses_json_and_strips_fenced_blocks(monkeypatch):
    fake_client = FakeAsyncClient()
    monkeypatch.setattr(cluster.httpx, "AsyncClient", lambda *args, **kwargs: fake_client)

    result = await cluster._call_gemini("um prompt qualquer")

    assert result == {"foo": "bar"}


@pytest.mark.asyncio
async def test_call_gemini_limits_output_and_enables_grounding(monkeypatch):
    fake_client = FakeAsyncClient()
    monkeypatch.setattr(cluster.httpx, "AsyncClient", lambda *args, **kwargs: fake_client)

    result = await cluster._call_gemini(
        "um prompt qualquer",
        model="gemini-2.5-flash-lite",
        max_output_tokens=123,
        use_google_search=True,
        purpose="fact_check_test",
    )

    request = fake_client.calls[0]
    assert request[1].endswith("models/gemini-2.5-flash-lite:generateContent")
    assert request[3]["generationConfig"]["maxOutputTokens"] == 123
    assert request[3]["generationConfig"]["thinkingConfig"] == {
        "thinkingBudget": cluster.settings.fact_check_thinking_budget
    }
    assert "responseMimeType" not in request[3]["generationConfig"]
    assert request[3]["tools"] == [{"google_search": {}}]
    assert result["_grounding_urls"] == []


@pytest.mark.asyncio
async def test_initial_triage_uses_compact_model_and_returns_only_eligibility(monkeypatch):
    captured = {}

    async def fake_call(prompt, **kwargs):
        captured["prompt"] = prompt
        captured["kwargs"] = kwargs
        return {
            "canonical_title": "Governo publica medida sobre apostas",
            "summary": "O governo publicou uma medida sobre apostas.",
            "categories": ["Política"],
            "verification": {
                "eligible": True,
                "claim": "O governo publicou uma medida sobre apostas.",
                "article_ids": ["article-1"],
                "reason": "Ato publicado.",
            },
        }

    monkeypatch.setattr(cluster, "_call_gemini", fake_call)
    articles = [
        {
            "id": "article-1",
            "title": "Governo publica medida",
            "lead": "Lead curto",
            "content": "Conteúdo do artigo",
        }
    ]

    result = await cluster._run_initial_triage(articles)

    assert result["verification"]["eligible"] is True
    assert result["verification"]["article_ids"] == ["article-1"]
    assert captured["kwargs"]["model"] == cluster.settings.gemini_fact_check_triage_model
    assert captured["kwargs"]["max_output_tokens"] == cluster.settings.fact_check_triage_max_output_tokens
    assert "não deve tentar decidir se ele é verdadeiro" in captured["prompt"]
    assert "Registro de candidatura" in captured["prompt"]
    assert "candidatos a deputado estadual ou federal" in captured["prompt"]


@pytest.mark.asyncio
async def test_official_verification_prompt_includes_source_catalog(monkeypatch):
    captured = {}

    async def fake_call(prompt, **kwargs):
        captured["prompt"] = prompt
        return {
            "verdict": "unverifiable",
            "confidence": 0,
            "source_url": "",
            "explanation": "",
            "_grounding_urls": [],
        }

    async def no_direct_sources(_claim):
        return []

    monkeypatch.setattr(cluster, "_call_gemini", fake_call)
    monkeypatch.setattr(cluster, "find_official_source_evidence", no_direct_sources)

    assert await cluster._verify_official_claim("Candidaturas foram registradas.") is None
    assert "DivulgaCandContas" in captured["prompt"]
    assert "tesourotransparente.gov.br" in captured["prompt"]


@pytest.mark.asyncio
async def test_official_verification_accepts_official_google_redirect_destination(monkeypatch):
    async def fake_call(_prompt, **_kwargs):
        return {
            "verdict": "true",
            "confidence": 0.95,
            "source_url": "https://www.gov.br/fazenda/dado",
            "explanation": "Dado oficial publicado.",
            "_grounding_urls": [
                "https://vertexaisearch.cloud.google.com/grounding-api-redirect/token"
            ],
        }

    async def fake_resolve(_urls):
        return {"https://www.gov.br/fazenda/dado"}

    async def no_direct_sources(_claim):
        return []

    monkeypatch.setattr(cluster, "_call_gemini", fake_call)
    monkeypatch.setattr(cluster, "_resolve_official_grounding_urls", fake_resolve)
    monkeypatch.setattr(cluster, "find_official_source_evidence", no_direct_sources)

    result = await cluster._verify_official_claim("O dado foi publicado.")

    assert result and result["verdict"] == "true"
    assert result["evidence"].startswith("Fonte oficial: https://www.gov.br/fazenda/dado.")


@pytest.mark.asyncio
async def test_official_verification_accepts_direct_official_api_evidence(monkeypatch):
    captured = {}

    async def fake_call(prompt, **_kwargs):
        captured["prompt"] = prompt
        return {
            "verdict": "true",
            "confidence": 0.95,
            "source_url": "https://api.bcb.gov.br/dados/serie/bcdata.sgs.432/dados/ultimos/6?formato=json",
            "explanation": "A série oficial informa 13,75 na observação exibida.",
            "_grounding_urls": [],
        }

    async def fake_sources(_claim):
        return [
            {
                "source_name": "Banco Central do Brasil",
                "source_url": "https://api.bcb.gov.br/dados/serie/bcdata.sgs.432/dados/ultimos/6?formato=json",
                "excerpt": "Série SGS 432; últimas observações retornadas pela API oficial: 04/11/2026: 13.75.",
                "kind": "direct_evidence",
            }
        ]

    monkeypatch.setattr(cluster, "_call_gemini", fake_call)
    monkeypatch.setattr(cluster, "find_official_source_evidence", fake_sources)

    result = await cluster._verify_official_claim("A taxa Selic está em 13,75%.")

    assert "EVIDÊNCIA DIRETA: Banco Central do Brasil" in captured["prompt"]
    assert result and result["verdict"] == "true"
    assert "api.bcb.gov.br" in result["evidence"]


@pytest.mark.asyncio
async def test_official_verification_rejects_catalog_lead_as_evidence(monkeypatch):
    async def fake_call(_prompt, **_kwargs):
        return {
            "verdict": "true",
            "confidence": 0.95,
            "source_url": "https://dadosabertos.tse.jus.br/dataset/candidatos-2024",
            "explanation": "O catálogo lista o conjunto de dados.",
            "_grounding_urls": [],
        }

    async def fake_sources(_claim):
        return [
            {
                "source_name": "TSE Dados Abertos",
                "source_url": "https://dadosabertos.tse.jus.br/dataset/candidatos-2024",
                "excerpt": "Candidatos - 2024.",
                "kind": "discovery_lead",
            }
        ]

    monkeypatch.setattr(cluster, "_call_gemini", fake_call)
    monkeypatch.setattr(cluster, "find_official_source_evidence", fake_sources)

    assert await cluster._verify_official_claim("Uma candidatura foi registrada.") is None


@pytest.mark.asyncio
async def test_direct_official_evidence_works_without_google_grounding(monkeypatch):
    async def fake_call(_prompt, **kwargs):
        assert kwargs["use_google_search"] is False
        return {
            "verdict": "true",
            "confidence": 0.95,
            "source_url": "https://api.bcb.gov.br/dados/serie/bcdata.sgs.432/dados/ultimos/6?formato=json",
            "explanation": "A observação direta confirma a taxa.",
            "_grounding_urls": [],
        }

    async def fake_sources(_claim):
        return [
            {
                "source_name": "Banco Central do Brasil",
                "source_url": "https://api.bcb.gov.br/dados/serie/bcdata.sgs.432/dados/ultimos/6?formato=json",
                "excerpt": "Série SGS 432: 04/11/2026: 13.75.",
                "kind": "direct_evidence",
            }
        ]

    monkeypatch.setattr(cluster.settings, "fact_check_enable_official_grounding", False)
    monkeypatch.setattr(cluster, "_call_gemini", fake_call)
    monkeypatch.setattr(cluster, "find_official_source_evidence", fake_sources)

    result = await cluster._verify_official_claim("A taxa Selic está em 13,75%.")

    assert result and result["verdict"] == "true"


@pytest.mark.asyncio
async def test_triage_creates_fallback_without_official_lookup(monkeypatch):
    articles = [
        {"id": "article-1", "title": "Lula expressa intenção sobre apostas"},
        {"id": "article-2", "title": "Outra declaração"},
    ]

    async def unexpected_lookup(_claim):
        raise AssertionError("não deve haver busca para matéria não elegível")

    monkeypatch.setattr(cluster, "_verify_official_claim", unexpected_lookup)

    result = await cluster._build_claims_from_triage(
        articles,
        {"verification": {"eligible": False}},
    )

    assert set(result) == {"article-1", "article-2"}
    assert all(claims[0]["verdict"] == "unverifiable" for claims in result.values())
    assert all("Não verificável por fontes oficiais" in claims[0]["evidence"] for claims in result.values())


@pytest.mark.asyncio
async def test_triage_shares_one_official_result_only_with_related_articles(monkeypatch):
    articles = [
        {"id": "article-1", "title": "Medida publicada"},
        {"id": "article-2", "title": "Comentário sobre a medida"},
    ]
    lookups = []

    async def fake_lookup(claim):
        lookups.append(claim)
        return {
            "claim": claim,
            "verdict": "true",
            "confidence": 0.9,
            "evidence": "Fonte oficial: https://www.gov.br/exemplo.",
        }

    monkeypatch.setattr(cluster, "_verify_official_claim", fake_lookup)

    result = await cluster._build_claims_from_triage(
        articles,
        {
            "verification": {
                "eligible": True,
                "claim": "Uma medida foi publicada.",
                "article_ids": ["article-1"],
            }
        },
    )

    assert lookups == ["Uma medida foi publicada."]
    assert result["article-1"][0]["verdict"] == "true"
    assert result["article-2"][0]["verdict"] == "unverifiable"


@pytest.mark.asyncio
async def test_triage_reuses_only_an_exact_grounded_claim(monkeypatch):
    articles = [{"id": "article-1", "title": "Medida publicada"}]
    previous_claims = [
        {
            "claim": "O governo publicou a Medida Provisória 123.",
            "verdict": "true",
            "confidence": 0.95,
            "evidence": "Fonte oficial: https://www.gov.br/planalto/mp-123.",
        }
    ]

    async def unexpected_lookup(_claim):
        raise AssertionError("claim oficial idêntica não deve disparar nova busca")

    monkeypatch.setattr(cluster, "_verify_official_claim", unexpected_lookup)

    result = await cluster._build_claims_from_triage(
        articles,
        {
            "verification": {
                "eligible": True,
                "claim": "O GOVERNO publicou a medida provisória 123",
                "article_ids": ["article-1"],
            }
        },
        previous_claims,
    )

    assert result["article-1"][0]["verdict"] == "true"
    assert result["article-1"][0]["evidence"] == previous_claims[0]["evidence"]


def test_fetch_official_claims_excludes_legacy_or_unverifiable_claims():
    db = FakeDB(
        {
            "claims": [
                {
                    "claim": "Ato publicado.",
                    "verdict": "true",
                    "confidence": 0.9,
                    "evidence": "Fonte oficial: https://www.gov.br/ato.",
                },
                {
                    "claim": "Claim legada.",
                    "verdict": "true",
                    "confidence": 0.9,
                    "evidence": "Segundo a reportagem.",
                },
                {
                    "claim": "Sem confirmação.",
                    "verdict": "unverifiable",
                    "confidence": 0.0,
                    "evidence": "Fonte oficial: https://www.gov.br/ato.",
                },
            ]
        }
    )

    claims = cluster._fetch_official_claims(db, "topic-1")

    assert claims == [db.table_data["claims"][0]]


def test_official_verification_requires_a_grounded_public_authority_url():
    accepted = cluster._normalize_official_verification(
        {
            "verdict": "true",
            "confidence": 0.9,
            "source_url": "https://www.gov.br/planalto/ato?utm=x",
            "explanation": "Ato publicado.",
            "_grounding_urls": ["https://www.gov.br/planalto/ato"],
        },
        "Ato foi publicado.",
    )
    rejected = cluster._normalize_official_verification(
        {
            "verdict": "true",
            "confidence": 0.9,
            "source_url": "https://example.com/ato",
            "_grounding_urls": ["https://example.com/ato"],
        },
        "Ato foi publicado.",
    )

    assert accepted and accepted["verdict"] == "true"
    assert "https://www.gov.br/planalto/ato" in accepted["evidence"]
    assert rejected is None


@pytest.mark.asyncio
async def test_process_skips_a_stale_debounced_task(monkeypatch):
    db = FakeDB(
        {
            "topics": [
                {
                    "id": "topic-1",
                    "initial_check": True,
                    "fact_check_next_at": "2026-09-18T12:10:00+00:00",
                }
            ]
        }
    )
    called = []

    async def fake_incremental(_db, topic_id):
        called.append(topic_id)

    monkeypatch.setattr(cluster, "get_client", lambda: db)
    monkeypatch.setattr(cluster, "_check_new_articles", fake_incremental)

    await cluster._process("topic-1", "2026-09-18T12:00:00+00:00")

    assert called == []


@pytest.mark.asyncio
async def test_process_skips_gemini_for_a_nonverifiable_topic(monkeypatch):
    db = FakeDB(
        {
            "topics": [
                {
                    "id": "topic-1",
                    "initial_check": True,
                    "fact_check_status": "unverifiable",
                }
            ]
        }
    )
    called = []

    async def fake_mark(_db, topic_id):
        called.append(topic_id)

    async def unexpected_triage(_db, _topic_id):
        raise AssertionError("tópico não verificável não deve chamar Gemini")

    monkeypatch.setattr(cluster, "get_client", lambda: db)
    monkeypatch.setattr(cluster, "_mark_new_articles_unverifiable", fake_mark)
    monkeypatch.setattr(cluster, "_check_new_articles", unexpected_triage)

    await cluster._process("topic-1")

    assert called == ["topic-1"]


@pytest.mark.asyncio
async def test_mark_new_articles_unverifiable_persists_checked_fallback():
    article = {
        "id": "article-1",
        "url": "https://example.com/article",
        "title": "Declaração sem registro oficial",
        "image_url": None,
    }
    db = FakeDB(
        {
            "topics": [{"id": "topic-1", "image_url": None}],
            "articles": [article],
        }
    )

    await cluster._mark_new_articles_unverifiable(db, "topic-1")

    assert any(
        call[0] == "upsert"
        and call[1][0]["verdict"] == "unverifiable"
        and call[1][0]["article_id"] == "article-1"
        for call in db.calls
    )
    assert any(
        call[0] == "update" and call[1] == {"checked": True}
        for call in db.calls
    )


def test_insert_claims_upserts_records(monkeypatch):
    db = FakeDB()
    cluster._insert_claims(
        db,
        "article-1",
        "topic-1",
        [{"claim": "Teste", "verdict": "true", "confidence": 0.8, "evidence": "Evidência"}],
    )
    assert any(
        call[0] == "upsert"
        and call[1][0]["article_id"] == "article-1"
        and call[1][0]["topic_id"] == "topic-1"
        and call[2]["on_conflict"] == "article_id, claim"
        and call[2]["ignore_duplicates"] is True
        for call in db.calls
    )


def test_normalize_claims_demotes_false_without_traceable_evidence():
    claims = cluster._normalize_claims(
        [
            {
                "claim": "O candidato desistiu definitivamente da campanha.",
                "verdict": "false",
                "confidence": 0.99,
                "evidence": "Uma análise anterior discorda desta alegação.",
            }
        ],
        {"https://example.com/source"},
    )

    assert claims[0]["verdict"] == "unverifiable"
    assert "não permite afirmar falsidade" in claims[0]["evidence"]


def test_normalize_initial_analysis_rejects_unknown_article_ids():
    analysis = cluster._normalize_initial_analysis(
        {
            "canonical_title": "Título neutro",
            "summary": "Resumo baseado nas fontes disponíveis.",
            "articles": [
                {"article_id": "unknown", "claims": []},
                {"article_id": "article-1", "claims": []},
            ],
        },
        [{"id": "article-1", "url": "https://example.com/1"}],
    )

    assert analysis["articles"] == [{"article_id": "article-1", "claims": []}]


def test_ensure_topic_image_updates_when_missing():
    db = FakeDB({"topics": [{"image_url": None}]})
    articles = [{"image_url": "https://cdn.example.com/topic.jpg"}]

    cluster._ensure_topic_image(db, "topic-1", articles)

    assert any(
        call[0] == "update" and call[1] == {"image_url": "https://cdn.example.com/topic.jpg"}
        for call in db.calls
    )


def test_ensure_topic_image_skips_when_already_present():
    db = FakeDB({"topics": [{"image_url": "https://cdn.example.com/existing.jpg"}]})
    articles = [{"image_url": "https://cdn.example.com/topic.jpg"}]

    cluster._ensure_topic_image(db, "topic-1", articles)

    assert not any(call[0] == "update" for call in db.calls)


@pytest.mark.asyncio
async def test_process_hot_topic_routes_to_initial_or_individual(monkeypatch):
    calls = []

    async def fake_initial(db, topic_id):
        calls.append(("initial", topic_id))

    async def fake_individual(db, topic_id):
        calls.append(("individual", topic_id))

    class TopicDB(FakeDB):
        def __init__(self, initial_check):
            super().__init__(
                {
                    "topics": [{"id": "topic-1", "initial_check": initial_check}]
                }
            )

    monkeypatch.setattr(cluster, "get_client", lambda: TopicDB(initial_check=False))
    monkeypatch.setattr(cluster, "_initial_check", fake_initial)
    monkeypatch.setattr(cluster, "_check_new_articles", fake_individual)

    await cluster._process("topic-1")
    assert calls == [("initial", "topic-1")]

    calls.clear()
    monkeypatch.setattr(cluster, "get_client", lambda: TopicDB(initial_check=True))
    await cluster._process("topic-1")
    assert calls == [("individual", "topic-1")]


def test_fetch_most_covered_topic_uses_recent_article_coverage():
    topics = [
        {"id": "topic-1", "canonical_title": "Maior cobertura", "article_count": 12},
        {"id": "topic-2", "canonical_title": "Outra cobertura", "article_count": 8},
    ]
    db = FakeDB(
        {
            "articles": [
                {"topic_id": "topic-1"},
                {"topic_id": "topic-1"},
                {"topic_id": "topic-2"},
                {"topic_id": "topic-2"},
                {"topic_id": "topic-2"},
            ],
            "topics": topics,
        }
    )

    result = cluster._fetch_most_covered_topic(db)

    assert result == topics[1]
    assert any(call[0] == "gte" and call[1][0] == "published_at" for call in db.calls)
    assert ("in_", ("id", ["topic-1", "topic-2"]), {}) in db.calls


def test_build_coverage_digest_push_payload_contract_v1_fields():
    payload = cluster._build_coverage_digest_push_payload(
        {"id": "topic-abc", "canonical_title": "Titulo IA", "article_count": 8}
    )

    assert payload["notification"]["title"] == "Titulo IA"
    assert payload["notification"]["body"] == "Tema com maior cobertura: 8 matérias nas últimas seis horas."
    assert payload["data"]["schemaVersion"] == "1"
    assert payload["data"]["type"] == "COVERAGE_DIGEST"
    assert payload["data"]["topicId"] == "topic-abc"
    assert payload["data"]["requiresPremium"] == "true"
    assert payload["data"]["targetScreen"] == "TopicDetail"
    assert payload["data"]["fallbackScreen"] == "Premium"
    assert payload["data"]["dedupKey"].startswith("coverage_digest_")
    assert payload["data"]["deeplink"] == "spectrum://topic/topic-abc"
    assert payload["data"]["topicCount"] == "1"


def test_fetch_active_push_tokens_deduplicates_and_skips_empty():
    db = FakeDB(
        {
            "device_push_tokens": [
                {"expo_push_token": "ExponentPushToken[a]"},
                {"expo_push_token": "ExponentPushToken[a]"},
                {"expo_push_token": ""},
                {"expo_push_token": "ExponentPushToken[b]"},
            ]
        }
    )

    tokens = cluster._fetch_active_push_tokens(db)
    assert tokens == ["ExponentPushToken[a]", "ExponentPushToken[b]"]


def test_build_expo_messages_maps_contract_payload():
    payload = cluster._build_coverage_digest_push_payload(
        {"id": "topic-1", "canonical_title": "Titulo IA", "article_count": 3}
    )
    messages = cluster._build_expo_messages(["ExponentPushToken[a]"], payload)

    assert messages[0]["to"] == "ExponentPushToken[a]"
    assert messages[0]["title"] == "Titulo IA"
    assert messages[0]["body"] == "Tema com maior cobertura: 3 matérias nas últimas seis horas."
    assert messages[0]["data"]["type"] == "COVERAGE_DIGEST"


def test_chunk_messages_respects_batch_size():
    messages = [{"to": f"ExponentPushToken[{i}]"} for i in range(205)]

    chunks = cluster._chunk_messages(messages)

    assert len(chunks) == 3
    assert len(chunks[0]) == 100
    assert len(chunks[1]) == 100
    assert len(chunks[2]) == 5


def test_extract_invalid_expo_tokens_maps_ticket_index():
    messages = [
        {"to": "ExponentPushToken[a]"},
        {"to": "ExponentPushToken[b]"},
    ]
    response = {
        "data": [
            {"status": "ok"},
            {"status": "error", "details": {"error": "DeviceNotRegistered"}},
        ]
    }

    invalid = cluster._extract_invalid_expo_tokens(messages, response)
    assert invalid == {"ExponentPushToken[b]"}


@pytest.mark.asyncio
async def test_dispatch_push_expo_marks_invalid_tokens_inactive(monkeypatch):
    db = FakeDB(
        {
            "device_push_tokens": [
                {"expo_push_token": "ExponentPushToken[a]", "is_active": True},
                {"expo_push_token": "ExponentPushToken[b]", "is_active": True},
            ]
        }
    )
    payload = cluster._build_coverage_digest_push_payload(
        {"id": "topic-1", "canonical_title": "Titulo IA", "article_count": 3}
    )

    monkeypatch.setattr(cluster.httpx, "AsyncClient", FakeExpoAsyncClient)

    await cluster._dispatch_push_expo(db, payload)

    assert any(
        call[0] == "update" and call[1] == {"is_active": False}
        for call in db.calls
    )


@pytest.mark.asyncio
async def test_send_coverage_digest_uses_webhook_provider(monkeypatch):
    db = FakeDB({"topics": [{"id": "topic-1", "is_hot": True, "initial_check": True}]})
    called = {"webhook": 0, "expo": 0}

    async def fake_webhook(_payload):
        called["webhook"] += 1

    async def fake_expo(_db, _payload):
        called["expo"] += 1

    monkeypatch.setattr(cluster.settings, "push_provider", "webhook")
    monkeypatch.setattr(
        cluster,
        "_fetch_most_covered_topic",
        lambda _db: {"id": "topic-1", "canonical_title": "Titulo IA", "article_count": 3},
    )
    monkeypatch.setattr(cluster, "_dispatch_push", fake_webhook)
    monkeypatch.setattr(cluster, "_dispatch_push_expo", fake_expo)

    monkeypatch.setattr(cluster, "get_client", lambda: db)
    await cluster._send_coverage_digest()

    assert called == {"webhook": 1, "expo": 0}


def test_validate_push_payload_accepts_valid_payload():
    db = FakeDB({"topics": [{"id": "topic-1", "is_hot": True, "initial_check": True}]})
    payload = cluster._build_coverage_digest_push_payload(
        {"id": "topic-1", "canonical_title": "Titulo IA", "article_count": 3}
    )

    is_valid, reason = cluster._validate_push_payload(db, payload)

    assert is_valid is True
    assert reason == "ok"


def test_validate_push_payload_rejects_invalid_fields():
    db = FakeDB({"topics": [{"id": "topic-1", "is_hot": True, "initial_check": True}]})
    payload = cluster._build_coverage_digest_push_payload(
        {"id": "topic-1", "canonical_title": "Titulo IA", "article_count": 3}
    )
    payload["notification"]["body"] = ""
    payload["data"]["sentAt"] = "invalid"

    is_valid, reason = cluster._validate_push_payload(db, payload)

    assert is_valid is False
    assert reason in {"body vazio", "sentAt inválido"}


def test_is_utc_iso8601_accepts_utc_and_rejects_naive():
    assert cluster._is_utc_iso8601("2026-07-09T12:00:00Z") is True
    assert cluster._is_utc_iso8601(datetime.now(timezone.utc).isoformat()) is True
    assert cluster._is_utc_iso8601("2026-07-09T12:00:00") is False


@pytest.mark.asyncio
async def test_initial_check_does_not_dispatch_an_immediate_push(monkeypatch):
    db = FakeDB(
        {
            "topics": [{"id": "topic-1", "is_hot": True, "initial_check": True}],
            "articles": [
                {
                    "id": "article-1",
                    "url": "https://example.com/1",
                    "title": "Titulo 1",
                    "lead": "Lead",
                    "content": "Conteudo",
                    "outlet_id": "o1",
                }
            ],
        }
    )

    async def fake_fetch_contents(_db, _articles):
        return None

    async def fake_run_initial_triage(_articles):
        return {
            "verification": {"eligible": False},
        }

    async def fake_run_initial_prompt(_articles):
        return {
            "canonical_title": "Titulo Editorial Original",
            "summary": "Resumo editorial original",
            "categories": ["Política"],
            "articles": [],
        }

    monkeypatch.setattr(cluster, "_fetch_contents", fake_fetch_contents)
    monkeypatch.setattr(cluster, "_run_initial_triage", fake_run_initial_triage)
    monkeypatch.setattr(cluster, "_run_initial_prompt", fake_run_initial_prompt)

    await cluster._initial_check(db, "topic-1")

    assert not any(call[0] == "table" and call[1] == "device_push_tokens" for call in db.calls)
    assert any(
        call[0] == "update"
        and call[1].get("canonical_title") == "Titulo Editorial Original"
        and call[1].get("fact_check_status") == "unverifiable"
        for call in db.calls
    )
