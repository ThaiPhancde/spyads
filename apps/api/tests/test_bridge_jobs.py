"""Bridge provider end to end: POST /allegro/search provider=bridge -> extension report on /api/imports/allegro -> job + rows.
Throw-away SQLite (conftest) + temp allegro.json; no network anywhere (bridge provider never calls SerpApi / a webhook)."""
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.eu_market import routes
from app.eu_market.models import EUBotJob, EUListing
from app.routers import bridge

TOKEN = "test-token-not-real"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("TOOLSPY_BRIDGE_TOKEN", TOKEN)
    monkeypatch.setattr(bridge, "DATA_PATH", tmp_path / "allegro.json")
    app = FastAPI()
    app.include_router(bridge.router)
    app.include_router(routes.router)
    return TestClient(app)


def rec(oid, price=19.99, ts="2026-10-12T10:00:00Z", **kw):
    return {"offer_id": oid, "title": f"Bransoletka {oid}", "price": price, "currency": "PLN", "seller": "sklep_a",
            "image_url": "https://a.allegroimg.com/x.jpg", "product_url": f"https://allegro.pl/oferta/bransoletka-{oid}", "fetched_at": ts, **kw}


def report(c, token=TOKEN, **body):
    return c.post("/api/imports/allegro", json={"records": [], **body}, headers={"X-ToolSpy-Token": token} if token else {})


def search(c, kw, **extra):
    r = c.post("/api/eu-market/allegro/search", json={"keyword": kw, "limit": 20, "provider": "bridge", **extra})
    assert r.status_code == 200, r.text
    return r.json()


def listings(*oids):
    with SessionLocal() as db:
        return {x.source_product_id: x for x in db.scalars(select(EUListing).where(
            EUListing.source == "allegro", EUListing.market_country == "PL", EUListing.source_product_id.in_(oids))).all()}


def test_records_land_in_eu_listings_dedupe(client):
    r = report(client, records=[rec("31000000001"), rec("31000000001", 25.0, "2026-10-12T11:00:00Z"), rec("31000000002")],
               keyword="kubek").json()
    assert (r["accepted"], r["written"]) == (2, 2)
    report(client, records=[rec("31000000001", 27.5, "2026-10-12T12:00:00Z")])  # same offer again -> update, not a new row
    rows = listings("31000000001", "31000000002")
    assert len(rows) == 2
    a = rows["31000000001"]
    assert (a.price, a.currency, a.data_access, a.seller_name, a.url) == (27.5, "PLN", "browser_bridge", "sklep_a",
                                                                          "https://allegro.pl/oferta/bransoletka-31000000001")
    # click-tracking link is never stored: the offer URL is rebuilt from the id
    report(client, records=[rec("31000000003", product_url="https://allegro.pl/events/clicks?x=1", url_unresolved=True)])
    assert listings("31000000003")["31000000003"].url == "https://allegro.pl/oferta/31000000003"


def test_bridge_job_done_with_rows_and_reuse(client, monkeypatch):
    monkeypatch.delenv("SERPAPI_API_KEY", raising=False)  # bridge needs no SerpApi / Grok config
    j = search(client, "Lampka  nocna")
    assert (j["status"], j["provider"], j["final"], j["reused"], j["rows"]) == ("queued", "bridge", False, None, [])
    assert search(client, "lampka NOCNA")["id"] == j["id"]  # in-flight bridge job reused
    r = report(client, job_id=j["id"], records=[rec("32000000001"), rec("32000000002")])
    assert r.status_code == 200, r.text
    assert (r.json()["job"]["status"], r.json()["job"]["items_written"]) == ("done", 2)
    g = client.get(f"/api/eu-market/allegro/jobs/{j['id']}").json()
    assert (g["status"], g["final"], g["provider"], g["items_written"], g["items_received"]) == ("done", True, "bridge", 2, 2)
    assert len(g["rows"]) >= 1 and g["rows"][0]["offers"][0]["currency"] == "PLN"
    assert {l.query for l in listings("32000000001", "32000000002").values()} == {"Lampka nocna"}  # job keyword used as query
    hit = search(client, "lampka nocna")  # cache hit: the done job with its rows, no new job
    assert (hit["id"], hit["reused"], hit["status"]) == (j["id"], "cache", "done") and hit["rows"]
    assert search(client, "lampka nocna", force=True)["id"] != j["id"]
    # a second report for the finished job -> 409, records still saved
    r = report(client, job_id=j["id"], records=[rec("32000000003")])
    assert r.status_code == 409 and r.json()["detail"]["code"] == "job_not_open" and "32000000003" in listings("32000000003")


def test_blocked_captcha_error_empty(client):
    for kw, status, want in (("zegar a", "blocked", "blocked"), ("zegar b", "captcha", "captcha"), ("zegar c", "error", "failed"),
                             ("zegar d", "ok", "empty")):
        j = search(client, kw)
        r = report(client, job_id=j["id"], status=status, error="DataDome" if status != "ok" else None)
        assert r.status_code == 200 and r.json()["written"] == 0, r.text
        g = client.get(f"/api/eu-market/allegro/jobs/{j['id']}").json()
        assert (g["status"], g["final"], g["rows"]) == (want, True, []), g
        assert g["message"]
    assert g["error_code"] is None  # empty is not an error
    j = search(client, "zegar e")  # every record rejected -> failed with a reason, not "done"
    g = report(client, job_id=j["id"], records=[{"offer_id": "abc", "title": "x"}]).json()["job"]
    assert (g["status"], g["error_code"]) == ("failed", "invalid_items")


def test_unauthorized_and_unknown_job(client):
    j = search(client, "pierscionek")
    assert report(client, token=None, job_id=j["id"], records=[rec("33000000001")]).status_code == 401
    assert report(client, token="wrong", job_id=j["id"], records=[rec("33000000001")]).status_code == 401
    assert client.get(f"/api/eu-market/allegro/jobs/{j['id']}").json()["status"] == "queued"
    assert not listings("33000000001") and not bridge.DATA_PATH.exists()
    r = report(client, job_id="nope", records=[rec("33000000002")])
    assert r.status_code == 404 and r.json()["detail"]["code"] == "job_not_found"
    assert r.json()["detail"]["import"]["written"] == 1 and listings("33000000002")  # data kept despite the bad job id
    assert report(client, job_id=j["id"], status="maybe").status_code == 422


def test_timeout_then_late_report(client):
    j = search(client, "kolczyki")
    with SessionLocal() as db:
        assert db.get(EUBotJob, j["id"]).deadline_at - datetime.utcnow() <= timedelta(minutes=3)
        db.get(EUBotJob, j["id"]).deadline_at = datetime.utcnow() - timedelta(seconds=1)
        db.commit()
    assert client.get(f"/api/eu-market/allegro/jobs/{j['id']}").json()["status"] == "timeout"
    r = report(client, job_id=j["id"], records=[rec("34000000001")])
    assert r.status_code == 409 and r.json()["detail"]["status"] == "timeout" and listings("34000000001")


def test_in_flight_reuse_is_per_provider(client, monkeypatch):
    from app.eu_market import allegro_bot
    monkeypatch.setenv("SERPAPI_API_KEY", "fake-never-called")  # request_search(serp) only creates the job; run_serp is not called here
    b = search(client, "kubek reuse")
    with SessionLocal() as db:
        s = allegro_bot.request_search(db, "kubek reuse", 20, provider="serp")
    assert (s["provider"], s["status"], s["reused"]) == ("serp", "running", None) and s["id"] != b["id"]
    assert search(client, "KUBEK reuse")["id"] == b["id"]  # a bridge retry still attaches to the live bridge job
