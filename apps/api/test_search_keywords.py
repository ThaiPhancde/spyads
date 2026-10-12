"""Multi-keyword search self-check (read-only on the local DB): .venv/Scripts/python test_search_keywords.py"""
import os

os.environ.setdefault("ADMIN_TOKEN", "test-admin")  # writes need X-Admin-Token (AdminGuard, main.py)
os.environ.setdefault("DISABLE_SCHEDULER", "1")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.routers.intel import split_keywords  # noqa: E402

assert split_keywords("Fengshui | lucky bracelet, pixiu\nfengshui") == ["fengshui", "lucky bracelet", "pixiu"]
c = TestClient(app)
one = c.get("/api/search?q=bracelet&limit=200").json()
both = c.get("/api/search?q=bracelet|massager&limit=200").json()
ex = c.get("/api/search?q=bracelet|massager&exclude=kids&limit=200").json()
assert both["total"] >= one["total"], "OR must never return fewer than one of its keywords"
assert ex["total"] <= both["total"], "exclude must never add products"
assert all(r["matched_keywords"] for r in both["rows"]), "every hit says which keyword matched"
admin = {"X-Admin-Token": os.environ["ADMIN_TOKEN"]}
assert c.post("/api/search/live", json={"query": " , |"}).status_code == 401, "no token → guard blocks before validation"
assert c.post("/api/search/live", json={"query": " , |"}, headers=admin).status_code == 400, "empty keywords → 400, no job created"
print("ok", one["total"], both["total"], ex["total"])
