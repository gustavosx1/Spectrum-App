from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import worker.config
from api.main import app


class FakeTable:
    def __init__(self, rows):
        self.rows = rows
        self._single = False
        self._update_values = None
        self._upsert_payload = None
        self._upsert_kwargs = None

    def select(self, *args, **kwargs):
        return self

    def eq(self, *args, **kwargs):
        return self

    def single(self):
        self._single = True
        return self

    def order(self, *args, **kwargs):
        return self

    def range(self, *args, **kwargs):
        return self

    def in_(self, *args, **kwargs):
        return self

    def update(self, values):
        self._update_values = values
        return self

    def upsert(self, values, **kwargs):
        self._upsert_payload = values
        self._upsert_kwargs = kwargs
        if isinstance(values, list):
            self.rows.extend(values)
        else:
            self.rows.append(values)
        return self

    def execute(self):
        if self._single:
            return SimpleNamespace(data=self.rows[0] if self.rows else None)
        return SimpleNamespace(data=self.rows)


class FakeDB:
    def __init__(self, tables):
        self.tables = tables

    def table(self, name):
        if name not in self.tables:
            self.tables[name] = []
        return FakeTable(self.tables.get(name, []))


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr("api.middleware.auth._verify_token", lambda token: {"sub": "user-123"})

    fake_db = FakeDB(
        {
            "user_profiles": [
                {
                    "id": "user-123",
                    "email": "ana@example.com",
                    "is_premium": True,
                    "premium_expires_at": None,
                    "created_at": "2024-01-01T00:00:00",
                    "premium_platform": "ios",
                    "premium_product_id": "monthly",
                    "premium_auto_renews": True,
                }
            ],
            "topics": [
                {
                    "id": "topic-1",
                    "canonical_title": "Título do tópico",
                    "summary": "Resumo",
                    "article_count": 2,
                    "is_hot": True,
                    "initial_check": True,
                    "created_at": "2024-01-02T00:00:00",
                }
            ],
            "articles": [
                {"id": "art-1", "topic_id": "topic-1", "outlet_id": "out-1", "url": "https://a", "title": "A", "lead": "lead", "image_url": None, "author": "Ana", "published_at": "2024-01-03T00:00:00", "political_lean": "left", "checked": True},
                {"id": "art-2", "topic_id": "topic-1", "outlet_id": "out-2", "url": "https://b", "title": "B", "lead": "lead", "image_url": None, "author": "Beto", "published_at": "2024-01-04T00:00:00", "political_lean": "right", "checked": False},
            ],
            "outlets": [
                {"id": "out-1", "name": "Outlet A", "political_score": 10},
                {"id": "out-2", "name": "Outlet B", "political_score": 90},
            ],
            "claims": [
                {"id": "claim-1", "article_id": "art-1", "claim": "Claim 1", "verdict": "true", "confidence": 0.9, "evidence": "evidence"}
            ],
            "device_push_tokens": [],
        }
    )

    monkeypatch.setattr("api.auth.router.get_client", lambda: fake_db)
    monkeypatch.setattr("api.feed.router.get_client", lambda: fake_db)
    monkeypatch.setattr("api.notifications.router.get_client", lambda: fake_db)
    monkeypatch.setattr("api.feed.router.require_premium", lambda request: None)
    monkeypatch.setattr("api.utils.premium.get_client", lambda: fake_db)
    monkeypatch.setattr("api.payments.router.get_user_id", lambda request: "user-123")

    with TestClient(app) as test_client:
        yield test_client


def test_health_endpoint(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"health": "ok"}


def test_profile_endpoint_returns_user_profile(client):
    response = client.get("/auth/me", headers={"Authorization": "Bearer token"})
    assert response.status_code == 200
    body = response.json()
    assert body["email"] == "ana@example.com"
    assert body["is_premium"] is True


def test_subscription_endpoint_returns_subscription_status(client):
    response = client.get("/auth/subscription", headers={"Authorization": "Bearer token"})
    assert response.status_code == 200
    body = response.json()
    assert body["is_premium"] is True
    assert body["platform"] == "ios"


def test_topics_endpoint_returns_blindspot_and_topic_list(client):
    response = client.get("/feed/topics", headers={"Authorization": "Bearer token"})
    assert response.status_code == 200
    body = response.json()
    assert body["meta"]["limit"] == 20
    assert body["meta"]["offset"] == 0
    assert body["meta"]["has_more"] is False
    assert len(body["data"]) == 1
    assert body["data"][0]["canonical_title"] == "Título do tópico"
    assert body["data"][0]["blindspot"]["dominant_side"] is None


def test_get_topic_endpoint_returns_grouped_articles(client):
    response = client.get("/feed/topics/topic-1", headers={"Authorization": "Bearer token"})
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "topic-1"
    assert len(body["articles_left"]) == 1
    assert len(body["articles_right"]) == 1


def test_payment_status_endpoint_returns_subscription(client):
    response = client.get("/payments/status", headers={"Authorization": "Bearer token"})
    assert response.status_code == 200
    body = response.json()
    assert body["is_premium"] is True


def test_verify_purchase_endpoint_activates_premium(monkeypatch, client):
    async def fake_verify_apple(receipt):
        return {"is_valid": True, "expires_at": "2024-06-01T00:00:00+00:00"}

    called = {}

    def fake_activate(user_id, platform, product_id, expires_at, auto_renews=True):
        called["payload"] = {
            "user_id": user_id,
            "platform": platform,
            "product_id": product_id,
            "expires_at": expires_at,
            "auto_renews": auto_renews,
        }

    monkeypatch.setattr("api.payments.router._verify_apple", fake_verify_apple)
    monkeypatch.setattr("api.payments.router.activate_premium", fake_activate)

    response = client.post(
        "/payments/verify",
        json={"platform": "ios", "receipt_token": "receipt", "product_id": "monthly"},
        headers={"Authorization": "Bearer token"},
    )

    assert response.status_code == 200
    assert response.json()["is_valid"] is True
    assert called["payload"]["user_id"] == "user-123"


class FakeAsyncClient:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, data=None, headers=None):
        return SimpleNamespace(
            status_code=200,
            json=lambda: {
                "access_token": "new-access-token",
                "refresh_token": "new-refresh-token",
                "expires_in": 3600,
                "token_type": "bearer",
            },
        )


def test_refresh_token_endpoint_renews_token(monkeypatch, client):
    monkeypatch.setattr("api.auth.router.httpx.AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(worker.config.settings, "supabase_service_role_key", "test-service-role-key")

    response = client.post(
        "/auth/refresh",
        json={"refresh_token": "old-refresh-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["access_token"] == "new-access-token"
    assert body["refresh_token"] == "new-refresh-token"
    assert body["expires_in"] == 3600
    assert body["token_type"] == "bearer"


def test_register_push_token_endpoint_registers_expo_token(client):
    response = client.post(
        "/notifications/token",
        json={
            "expo_push_token": "ExponentPushToken[valid-token-123]",
            "platform": "ios",
        },
        headers={"Authorization": "Bearer token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True


def test_register_push_token_endpoint_validates_input(client):
    response = client.post(
        "/notifications/token",
        json={
            "expo_push_token": "invalid",
            "platform": "ios",
        },
        headers={"Authorization": "Bearer token"},
    )
    assert response.status_code == 400


def test_register_push_token_endpoint_validates_platform(client):
    response = client.post(
        "/notifications/token",
        json={
            "expo_push_token": "ExponentPushToken[valid-token-123]",
            "platform": "web",
        },
        headers={"Authorization": "Bearer token"},
    )

    assert response.status_code == 400


def test_unregister_push_token_endpoint_deactivates_token(client):
    response = client.request(
        "DELETE",
        "/notifications/token",
        json={"expo_push_token": "ExponentPushToken[valid-token-123]"},
        headers={"Authorization": "Bearer token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True


def test_unregister_push_token_endpoint_validates_input(client):
    response = client.request(
        "DELETE",
        "/notifications/token",
        json={"expo_push_token": "invalid"},
        headers={"Authorization": "Bearer token"},
    )

    assert response.status_code == 400
