
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

    async def fake_call(prompt):
        captured["prompt"] = prompt
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


@pytest.mark.asyncio
async def test_run_individual_prompt_includes_existing_claims(monkeypatch):
    captured = {}

    async def fake_call(prompt):
        captured["prompt"] = prompt
        return {"claims": [{"claim": "Afirmacao", "verdict": "true"}]}

    monkeypatch.setattr(cluster, "_call_gemini", fake_call)

    article = {
        "id": "article-2",
        "title": "Outro título",
        "lead": "Lead extra",
        "content": "Conteúdo atualizado.",
    }
    existing_claims = [
        {"claim": "Reclamação anterior", "verdict": "partial", "evidence": "Explicação breve."}
    ]

    result = await cluster._run_individual_prompt(article, existing_claims)

    assert result == [{"claim": "Afirmacao", "verdict": "true"}]
    assert "Reclamação anterior" in captured["prompt"]
    assert "Outro título" in captured["prompt"]


@pytest.mark.asyncio
async def test_call_gemini_parses_json_and_strips_fenced_blocks(monkeypatch):
    fake_client = FakeAsyncClient()
    monkeypatch.setattr(cluster.httpx, "AsyncClient", lambda *args, **kwargs: fake_client)

    result = await cluster._call_gemini("um prompt qualquer")

    assert result == {"foo": "bar"}


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


def test_build_new_topic_push_payload_contract_v1_fields():
    payload = cluster._build_new_topic_push_payload("topic-abc", "Titulo IA")

    assert payload["notification"]["title"] == "Titulo IA"
    assert payload["notification"]["body"] == "Venha ver todos os lados desta história"
    assert payload["data"]["schemaVersion"] == "1"
    assert payload["data"]["type"] == "NEW_TOPIC"
    assert payload["data"]["topicId"] == "topic-abc"
    assert payload["data"]["requiresPremium"] == "true"
    assert payload["data"]["targetScreen"] == "TopicDetail"
    assert payload["data"]["fallbackScreen"] == "Premium"
    assert payload["data"]["dedupKey"] == "topic_topic-abc_v1"
    assert payload["data"]["deeplink"] == "spectrum://topic/topic-abc"


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
    payload = cluster._build_new_topic_push_payload("topic-1", "Titulo IA")
    messages = cluster._build_expo_messages(["ExponentPushToken[a]"], payload)

    assert messages[0]["to"] == "ExponentPushToken[a]"
    assert messages[0]["title"] == "Titulo IA"
    assert messages[0]["body"] == "Venha ver todos os lados desta história"
    assert messages[0]["data"]["type"] == "NEW_TOPIC"


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
    payload = cluster._build_new_topic_push_payload("topic-1", "Titulo IA")

    monkeypatch.setattr(cluster.httpx, "AsyncClient", FakeExpoAsyncClient)

    await cluster._dispatch_push_expo(db, payload)

    assert any(
        call[0] == "update" and call[1] == {"is_active": False}
        for call in db.calls
    )


@pytest.mark.asyncio
async def test_send_new_topic_push_uses_webhook_provider(monkeypatch):
    db = FakeDB({"topics": [{"id": "topic-1", "is_hot": True, "initial_check": True}]})
    called = {"webhook": 0, "expo": 0}

    async def fake_webhook(_payload):
        called["webhook"] += 1

    async def fake_expo(_db, _payload):
        called["expo"] += 1

    monkeypatch.setattr(cluster.settings, "push_provider", "webhook")
    monkeypatch.setattr(cluster, "_dispatch_push", fake_webhook)
    monkeypatch.setattr(cluster, "_dispatch_push_expo", fake_expo)

    await cluster._send_new_topic_push(db, "topic-1", "Titulo IA")

    assert called == {"webhook": 1, "expo": 0}


def test_validate_push_payload_accepts_valid_payload():
    db = FakeDB({"topics": [{"id": "topic-1", "is_hot": True, "initial_check": True}]})
    payload = cluster._build_new_topic_push_payload("topic-1", "Titulo IA")

    is_valid, reason = cluster._validate_push_payload(db, payload)

    assert is_valid is True
    assert reason == "ok"


def test_validate_push_payload_rejects_invalid_fields():
    db = FakeDB({"topics": [{"id": "topic-1", "is_hot": True, "initial_check": True}]})
    payload = cluster._build_new_topic_push_payload("topic-1", "")
    payload["data"]["sentAt"] = "invalid"

    is_valid, reason = cluster._validate_push_payload(db, payload)

    assert is_valid is False
    assert reason in {"title vazio", "sentAt inválido"}


def test_is_utc_iso8601_accepts_utc_and_rejects_naive():
    assert cluster._is_utc_iso8601("2026-07-09T12:00:00Z") is True
    assert cluster._is_utc_iso8601(datetime.now(timezone.utc).isoformat()) is True
    assert cluster._is_utc_iso8601("2026-07-09T12:00:00") is False


@pytest.mark.asyncio
async def test_initial_check_triggers_push_dispatch(monkeypatch):
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

    async def fake_run_initial_prompt(_articles):
        return {
            "canonical_title": "Titulo IA Final",
            "summary": "Resumo",
            "articles": [{"article_id": "article-1", "claims": []}],
        }

    sent = {}

    async def fake_send_push(_db, topic_id, ai_title):
        sent["topic_id"] = topic_id
        sent["ai_title"] = ai_title

    monkeypatch.setattr(cluster, "_fetch_contents", fake_fetch_contents)
    monkeypatch.setattr(cluster, "_run_initial_prompt", fake_run_initial_prompt)
    monkeypatch.setattr(cluster, "_send_new_topic_push", fake_send_push)

    await cluster._initial_check(db, "topic-1")

    assert sent == {"topic_id": "topic-1", "ai_title": "Titulo IA Final"}
