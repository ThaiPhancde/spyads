import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import bridge

TOKEN = "test-token-not-real"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("TOOLSPY_BRIDGE_TOKEN", TOKEN)
    monkeypatch.setattr(bridge, "DATA_PATH", tmp_path / "allegro.json")
    app = FastAPI()
    app.include_router(bridge.router)
    return TestClient(app)


def rec(oid="12345678901", ts="2026-10-12T10:00:00Z", **kw):
    return {"offer_id": oid, "title": "Bransoletka test", "price": 19.99, "currency": "PLN",
            "product_url": f"https://allegro.pl/oferta/bransoletka-{oid}", "fetched_at": ts, **kw}


def post(c, records, token=TOKEN):
    h = {"X-ToolSpy-Token": token} if token else {}
    return c.post("/api/imports/allegro", json={"records": records}, headers=h)


def test_token_required(client):
    assert post(client, [rec()], token=None).status_code == 401
    assert post(client, [rec()], token="wrong").status_code == 401
    assert not bridge.DATA_PATH.exists()


def test_token_not_configured(client, monkeypatch):
    monkeypatch.delenv("TOOLSPY_BRIDGE_TOKEN")
    assert post(client, [rec()]).status_code == 503


def test_add_dedupe_and_newer_wins(client):
    r = post(client, [rec(), rec(), rec("98765432101")]).json()
    assert (r["received"], r["added"], r["updated"], r["total"]) == (3, 2, 0, 2)
    r = post(client, [rec(ts="2026-10-12T11:00:00Z", price=25)]).json()
    assert (r["added"], r["updated"], r["total"]) == (0, 1, 2)
    r = post(client, [rec(ts="2026-10-01T00:00:00Z", price=1)]).json()  # older -> ignored
    assert (r["updated"], r["unchanged"]) == (0, 1)
    rows = json.loads(bridge.DATA_PATH.read_text(encoding="utf-8"))
    assert len(rows) == 2 and {x["offer_id"]: x["price"] for x in rows}["12345678901"] == 25


def test_preserves_existing_rows(client):
    bridge.DATA_PATH.write_text(json.dumps([{"offer_id": "111", "title": "old", "extra": 1}]), encoding="utf-8")
    r = post(client, [rec()]).json()
    assert r["total"] == 2
    assert json.loads(bridge.DATA_PATH.read_text(encoding="utf-8"))[0]["extra"] == 1


def test_rejects_incomplete(client):
    bad = [{"title": "x", "product_url": "https://allegro.pl/oferta/x-1"}, {**rec(), "title": ""},
           {**rec(), "product_url": None}, rec(oid="abc"), {**rec(), "product_url": "https://evil.example/oferta/1"}]
    r = post(client, bad + [rec()]).json()
    assert r["added"] == 1 and len(r["rejected"]) == 5


def test_main_app_wiring():
    """Route registered in the real app, AdminGuard exempts it, chrome-extension origin passes CORS."""
    try:
        from app.main import app
    except Exception as e:  # noqa: BLE001  (DB/env not available in this environment)
        pytest.skip(f"app.main import failed: {e}")
    c = TestClient(app)
    origin = "chrome-extension://abcdefghijklmnopabcdefghijklmnop"
    pre = c.options("/api/imports/allegro", headers={"Origin": origin, "Access-Control-Request-Method": "POST",
                                                     "Access-Control-Request-Headers": "x-toolspy-token,content-type"})
    assert pre.headers.get("access-control-allow-origin") == origin
    r = c.post("/api/imports/allegro", json={"records": []}, headers={"X-ToolSpy-Token": "definitely-wrong"})
    assert r.status_code == 401 and "bridge token" in r.text  # reached the route, not AdminGuard
