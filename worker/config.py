from __future__ import annotations

from typing import Optional
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_env: str = "development"
    api_cors_origins: str = "http://localhost:3000,http://localhost:5173"

    redis_url: str = "redis://localhost:6379/0"
    gemini_api_key: str
    gemini_model: str = "gemini-2.5-flash"

    supabase_url: str
    supabase_key: str
    supabase_jwt_secret: Optional[str] = None
    supabase_service_role_key: Optional[str] = None
    supabase_jwk_public_key: Optional[str] = None
    jwt_expected_audience: Optional[str] = "authenticated"
    jwt_expected_issuer: Optional[str] = None

    # threshold para agrupar artigos sobre o mesmo tópico
    topic_similarity_threshold: float = 0.17
    hot_topic_threshold: int = 3

    topic_window_hours: int = 24
    apple_shared_secret: str = ""  # App Store Connect → In-App Purchases

    # Pagamentos — Google
    android_package_name: str = ""  # ex: com.spectrum.app
    google_service_account_json: str = ""  # JSON da service account do Play Console

    # RevenueCat (abstrai iOS + Android)
    revenuecat_webhook_secret: str = ""

    # Push notifications (webhook/provider relay)
    push_provider: str = "expo"  # expo | webhook
    push_webhook_url: str = ""
    push_webhook_bearer: str = ""
    push_webhook_timeout_seconds: int = 10
    push_expo_send_url: str = "https://exp.host/--/api/v2/push/send"
    push_expo_access_token: str = ""
    push_device_table: str = "device_push_tokens"
    push_token_column: str = "expo_push_token"
    push_user_id_column: str = "user_id"
    push_active_column: str = "is_active"
    push_locale: str = "pt-BR"
    push_ai_title_version: str = "gpt-title-v2"

    model_config = {"env_file": ".env"}

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"

    @property
    def cors_origins(self) -> list[str]:
        origins = [origin.strip() for origin in self.api_cors_origins.split(",")]
        return [origin for origin in origins if origin]


settings = Settings()
