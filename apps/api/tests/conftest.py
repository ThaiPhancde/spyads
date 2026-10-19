"""Test isolation, applied before any `app` import: throw-away SQLite DB (never the real Postgres / apps/api/data) and paid
providers blanked (app/__init__ loads .env only for keys not already set, so "" wins) — no test can spend SerpApi /
Bright Data quota or hit a webhook."""
import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="toolspy-test-")
_pg_test = os.getenv("TOOLSPY_TEST_DATABASE_URL")
if _pg_test:
    from sqlalchemy.engine import make_url
    assert make_url(_pg_test).database.startswith("toolspy_test_"), "Only an isolated toolspy_test_* database is allowed"
os.environ["DATABASE_URL"] = _pg_test or f"sqlite:///{_tmp}/test.db"
os.environ["DISABLE_SCHEDULER"] = "1"
for k in ("SERPAPI_API_KEY", "BRIGHTDATA_API_TOKEN", "GROK_ROUTINE_WEBHOOK_URL", "GROK_ROUTINE_WEBHOOK_KEY", "GEMINI_API_KEY",
          "GROQ_API_KEY", "CEREBRAS_API_KEY", "CLOUDFLARE_API_TOKEN", "ANTHROPIC_API_KEY"):
    os.environ[k] = ""
os.environ["BRIGHTDATA_ENABLED"] = "0"

from app import models  # noqa: E402,F401 — registers tables
from app.workspace import models as workspace_models  # noqa: E402,F401
from app.db import Base, engine  # noqa: E402
from app.eu_market import models as eu_models  # noqa: E402,F401

Base.metadata.create_all(engine)


import pytest


@pytest.fixture
def clean_catalog():
    """Reset the complete FK graph, including workspace/cluster tables, only in the disposable DB."""
    assert engine.dialect.name == "sqlite" or engine.url.database.startswith("toolspy_test_")
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


@pytest.fixture(autouse=True)
def _fresh_search_cache():
    """GET /api/search caches full results per process; every test builds a new DB whose ids repeat."""
    from app.routers import intel
    intel._SEARCH_CACHE.clear()
    yield
    intel._SEARCH_CACHE.clear()
