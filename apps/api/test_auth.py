"""Auth smoke check. Run: cd apps/api && .venv/Scripts/python.exe test_auth.py
Uses the real app without its startup hook (no scheduler / media workers); only reads the DB (connector 999999 does not exist)."""
import os

os.environ.setdefault("ADMIN_TOKEN", "test-admin")
os.environ.setdefault("INGEST_TOKEN", "test-ingest")
os.environ.setdefault("DISABLE_SCHEDULER", "1")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

c = TestClient(app)  # no `with` → startup event does not run
ADMIN = os.environ["ADMIN_TOKEN"]

r = c.delete("/api/collector/connectors/999999")
assert r.status_code in (401, 403), r.status_code
r = c.delete("/api/collector/connectors/999999", headers={"X-Admin-Token": "wrong"})
assert r.status_code in (401, 403), r.status_code
r = c.delete("/api/collector/connectors/999999", headers={"X-Admin-Token": ADMIN})
assert r.status_code not in (401, 403), r.status_code
r = c.post("/api/ingest/ads", json={"records": []})
assert r.status_code in (401, 403), r.status_code
r = c.post("/api/ingest/ads", json={"records": []}, params={"token": os.environ["INGEST_TOKEN"]})  # query param no longer accepted
assert r.status_code in (401, 403), r.status_code
r = c.get("/api/health")
assert r.status_code == 200 and "llm_reason" in r.json(), r.text
print("auth ok:", r.json())
