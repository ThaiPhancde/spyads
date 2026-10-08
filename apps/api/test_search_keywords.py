"""Multi-keyword search self-check (read-only on the local DB): .venv/Scripts/python test_search_keywords.py"""
from fastapi.testclient import TestClient

from app.main import app
from app.routers.intel import split_keywords

assert split_keywords("Fengshui | lucky bracelet, pixiu\nfengshui") == ["fengshui", "lucky bracelet", "pixiu"]
c = TestClient(app)
one = c.get("/api/search?q=bracelet&limit=200").json()
both = c.get("/api/search?q=bracelet|massager&limit=200").json()
ex = c.get("/api/search?q=bracelet|massager&exclude=kids&limit=200").json()
assert both["total"] >= one["total"], "OR must never return fewer than one of its keywords"
assert ex["total"] <= both["total"], "exclude must never add products"
assert all(r["matched_keywords"] for r in both["rows"]), "every hit says which keyword matched"
assert c.post("/api/search/live", json={"query": " , |"}).status_code == 400
print("ok", one["total"], both["total"], ex["total"])
