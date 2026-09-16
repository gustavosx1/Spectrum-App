import pytest

from worker.tasks import embed


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

    def limit(self, n):
        self.calls.append(("limit", (n,), {}))
        return self

    def single(self):
        self.calls.append(("single", (), {}))
        self._single = True
        return self

    def insert(self, payload):
        self.calls.append(("insert", payload))
        return self

    def execute(self):
        self.calls.append(("execute", (), {}))
        if self._single and isinstance(self.data, list):
            return DummyResult(self.data[0] if self.data else None)
        return DummyResult(self.data)


class FakeDB:
    def __init__(self, table_data=None):
        self.table_data = table_data or {}
        self.calls = []

    def table(self, name):
        self.calls.append(("table", name))
        return FakeQuery(self.table_data.get(name, []), self.calls)


@pytest.mark.asyncio
async def test_process_dispatches_cluster_for_new_article_after_initial_check(monkeypatch):
    db = FakeDB(
        {
            "articles": [],
            "topics": [
                {
                    "is_hot": True,
                    "article_count": 7,
                    "initial_check": True,
                }
            ],
        }
    )

    dispatched = []

    class DummyTask:
        @staticmethod
        def delay(topic_id):
            dispatched.append(topic_id)

    async def fake_generate_embedding(text):
        return [0.1, 0.2]

    monkeypatch.setattr(embed, "get_client", lambda: db)
    monkeypatch.setattr(embed, "generate_embedding", fake_generate_embedding)
    monkeypatch.setattr(embed, "_find_or_create_topic", lambda *_: "topic-1")

    from worker.tasks import cluster

    monkeypatch.setattr(cluster, "process_hot_topic", DummyTask)

    await embed._process(
        {
            "url": "https://example.com/new-article",
            "title": "Novo artigo",
            "lead": "Lead",
        }
    )

    assert dispatched == ["topic-1"]


@pytest.mark.asyncio
async def test_process_does_not_dispatch_cluster_for_non_hot_topic(monkeypatch):
    db = FakeDB(
        {
            "articles": [],
            "topics": [
                {
                    "is_hot": False,
                    "article_count": 2,
                    "initial_check": False,
                }
            ],
        }
    )

    dispatched = []

    class DummyTask:
        @staticmethod
        def delay(topic_id):
            dispatched.append(topic_id)

    async def fake_generate_embedding(text):
        return [0.1, 0.2]

    monkeypatch.setattr(embed, "get_client", lambda: db)
    monkeypatch.setattr(embed, "generate_embedding", fake_generate_embedding)
    monkeypatch.setattr(embed, "_find_or_create_topic", lambda *_: "topic-1")

    from worker.tasks import cluster

    monkeypatch.setattr(cluster, "process_hot_topic", DummyTask)

    await embed._process(
        {
            "url": "https://example.com/new-article-2",
            "title": "Outro artigo",
            "lead": "Lead",
        }
    )

    assert dispatched == []
