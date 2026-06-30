import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

os.environ.setdefault("GEMINI_API_KEY", "test-gemini-key")
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-supabase-key")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-supabase-service-key")
os.environ.setdefault("SUPABASE_JWT_SECRET", "test-jwt-secret")
