from __future__ import annotations

from typing import Optional
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    redis_url: str = "redis://localhost:6379/0"
    gemini_api_key: str
    gemini_model: str = "gemini-1.5-flash"

    supabase_url: str
    supabase_key: str
    supabase_jwt_secret: Optional[str] = None
    supabase_service_role_key: Optional[str] = None
    supabase_jwk_public_key: Optional[str] = None

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
    model_config = {"env_file": ".env"}


settings = Settings()
