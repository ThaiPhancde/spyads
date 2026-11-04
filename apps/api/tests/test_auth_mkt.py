"""MKT write permissions (design 2026-10-26 §0 "Quyền ghi của MKT", AUTH-403, RADAR-WATCH): daily MKT actions open, system actions
stay 403, attribution comes from the session (never body.user), Competitor Radar "watched" is the caller's own follows."""
import pytest

from app.db import SessionLocal
from app.main import app  # noqa: F401 — registers every table before clean_catalog recreates them
from app.models import Product
from test_allocation import users
from test_workspace import client, follow, seed_advertisers, write


@pytest.fixture(autouse=True)
def fresh(clean_catalog, monkeypatch):
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "0")


def test_mkt_daily_actions_open_and_system_actions_refused():
    users()
    c = client("mkt-a@example.test")
    missing = 999999
    opened = [("PATCH", f"/products/{missing}/name", {"name": "x"}), ("POST", f"/products/{missing}/name/confirm", None),
              ("POST", f"/products/{missing}/name/undo", None), ("POST", f"/products/{missing}/split", {"ad_ids": [1]}),
              ("POST", "/products/merge", {"source_id": missing, "target_id": missing + 1}),
              ("POST", f"/products/{missing}/merges/1/undo", None), ("POST", f"/creatives/{missing}/pin", None),
              ("POST", "/products/image-matches/review", {"source": "ad", "source_id": 1, "target_source": "ad", "target_id": 2, "status": "bad"}),
              ("POST", f"/products/{missing}/scan", None)]
    for method, path, body in opened:
        r = write(c, method, path, body)
        assert r.status_code in (400, 404, 422), (path, r.status_code, r.text)
    for path in ("/pipeline/run", f"/products/{missing}/lifecycle", f"/products/{missing}/explain"):
        assert write(c, "POST", path, {"to": "TEST"}).status_code == 403, path


def test_rename_is_attributed_to_the_session_user_not_body_user():
    users()
    (_, _, pid), = seed_advertisers(1)
    r = write(client("mkt-a@example.test"), "PATCH", f"/products/{pid}/name", {"name": "jade bracelet", "user": "mkt-b"})
    assert r.status_code == 200, r.text
    with SessionLocal() as db:
        assert db.get(Product, pid).name_history[-1]["by"] == "mkt-a"


def test_radar_watched_is_per_account():
    users()
    (aid, _, _), (other, _, _) = seed_advertisers(2)
    ca, cb = client("mkt-a@example.test"), client("mkt-b@example.test")
    radar = lambda c: c.get("/api/competitor-radar", params={"watched_only": "true", "limit": 500}).json()
    assert radar(ca)["rows"] == []  # also warms the shared 60 s cache: a follow must still show at once
    follow(ca, aid)
    a, b = radar(ca), radar(cb)
    assert [r["id"] for r in a["rows"]] == [aid] and a["watched"] == 1 and a["rows"][0]["watched"] is True
    assert b["rows"] == [] and b["watched"] == 0
    assert {r["id"]: r["watched"] for r in cb.get("/api/competitor-radar").json()["rows"]} == {aid: False, other: False}
    assert ca.get(f"/api/competitor-radar/{aid}").json()["advertiser"]["watched"] is True
    assert cb.get(f"/api/competitor-radar/{aid}").json()["advertiser"]["watched"] is False


def test_mkt_can_ask_the_ai_agent(monkeypatch):
    """/api/agent/ask is a read-only question over the database, open to any MKT (it was 403: the whole /api/agent/ prefix was system-only)."""
    from app.services import llm
    monkeypatch.setattr(llm, "available", lambda env="LLM_PROVIDERS": False)  # rules parser: no model call in the test
    users()
    c = client("mkt-a@example.test")
    r = write(c, "POST", "/agent/ask", {"question": "top sản phẩm ở Philippines"})
    assert r.status_code == 200 and r.json()["engine"] == "rules" and r.json()["spec"]["intent"], r.text
