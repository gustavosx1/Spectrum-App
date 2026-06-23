from __future__ import annotations

from supabase import create_client, Client
from worker.config import settings


def get_client() -> Client:
    return create_client(settings.supabase_url, settings.supabase_service_key)
