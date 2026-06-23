from __future__ import annotations

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    redis_url: str = "redis://localhost:6379/0"
    gemini_api_key: str
    gemini_model: str = "gemini-2.0-flash-lite"

    supabase_url: str
    supabase_service_key: str

    # threshold para agrupar artigos sobre o mesmo tópico
    topic_similarity_threshold: float = 0.12
    hot_topic_threshold: int = 3

    topic_window_hours = 24

    class Config:
        env_file = ".env"


settings = Settings()
