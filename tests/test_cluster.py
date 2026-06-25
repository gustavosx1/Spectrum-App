
import asyncio
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

    async def post(self, url, params=None, json=None):
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
