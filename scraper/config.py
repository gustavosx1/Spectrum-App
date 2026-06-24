from __future__ import annotations

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Janela de coleta (minutos) — mesmo valor usado para RSS
    collection_window_minutes: int = 75

    # Playwright timeouts (ms)
    playwright_page_timeout_ms: int = 30000
    playwright_wait_after_load_ms: int = 1500
    # Throttling
    request_delay_seconds: float = 2.0
    max_articles_per_run: int = 30

    model_config = {"extra": "allow", "env_file": ".env"}


settings = Settings()
