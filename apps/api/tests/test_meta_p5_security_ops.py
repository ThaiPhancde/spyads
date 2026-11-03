"""P5 — security & operations: credentials kept out of the raw lake, quarantine of rejected records, connector secrets
encrypted at rest (when CREDENTIALS_KEY is set), a per-market rate budget, and the operations KPIs."""
import gzip
import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import func, select

from app import crawl_queue as cq, ingest, realtime, secrets_box
from app.collector.adapters.meta import MetaLibraryConnector, map_library_ad
from app.collector.factory import ConnectorFactory
from app.db import SessionLocal
from app.models import Connector, Creative, Event, IngestBatch, IngestQuarantine
from app.storage import store, write_raw_batch


def raw(i, **extra):
    return {"ad_archive_id": str(i), "page_id": "p", "collation_id": f"g{i}",
            "snapshot": {"title": "Portable electric blender", "body": {"text": "Buy blender now"}}, **extra}


# ---------------------------------------------------------------- no credential in the raw lake
def test_raw_batch_never_keeps_cookies_tokens_or_auth_headers():
    rec = raw(1, cookie="sessionid=abc", fb_dtsg="dtsg-secret", headers={"Authorization": "Bearer xyz", "user-agent": "UA"},
              landing="https://shop.example/p?utm=1&access_token=tok-secret&id=7")
    key = write_raw_batch("meta_library", "ads", [rec])
    text = gzip.decompress(store().get(key)).decode()
    for secret in ("sessionid=abc", "dtsg-secret", "Bearer xyz", "tok-secret"):
        assert secret not in text
    kept = json.loads(text)
    assert kept["ad_archive_id"] == "1" and kept["headers"] == {"user-agent": "UA"} and "id=7" in kept["landing"]


# ---------------------------------------------------------------- quarantine
@pytest.fixture
def quiet(clean_catalog, monkeypatch):
    monkeypatch.setattr("app.media.enqueue", lambda *a: None)
    monkeypatch.setattr("app.services.engine.rescore_products", lambda *a: 0)


def test_rejected_records_are_quarantined_not_just_dropped(quiet, monkeypatch):
    from sqlalchemy.exc import IntegrityError
    original = ingest._upsert_ad

    def poison(db, r, *a):
        if r.source_ad_id == "2":
            raise IntegrityError("INSERT", {}, Exception("value too long"))
        return original(db, r, *a)
    monkeypatch.setattr(ingest, "_upsert_ad", poison)
    bad = map_library_ad(raw(3, cookie="sessionid=leak"), "meta_library", "US")
    bad.source_ad_id = ""  # fails validation
    with SessionLocal() as db:
        from app.collector.batches import ingest_batch
        res = ingest_batch(db, [map_library_ad(raw(1), "meta_library", "US"), map_library_ad(raw(2), "meta_library", "US"), bad])
        db.commit()
        rows = db.scalars(select(IngestQuarantine).order_by(IngestQuarantine.id)).all()
    assert res["new_ads"] == 1 and len(rows) == 2
    assert {r.reason.split(":")[0] for r in rows} == {"IntegrityError", "invalid"}
    assert all("sessionid=leak" not in json.dumps(r.payload) for r in rows)
    from app.retention import table_ttls
    assert table_ttls()["ingest_quarantine"] == ("created_at", 30)


# ---------------------------------------------------------------- connector secrets at rest
def client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.routers.intel import require_token, router
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_token] = lambda: None
    return TestClient(app)


def apify_connector():
    with SessionLocal() as db:
        c = Connector(name="Apify", provider="apify", kind="api", group="paid", adapter="apify_meta", enabled=True, config={})
        db.add(c)
        db.commit()
        return c.id


def test_without_a_key_secrets_stay_as_before_and_the_kpi_says_so(clean_catalog, monkeypatch):
    monkeypatch.delenv("CREDENTIALS_KEY", raising=False)
    cid = apify_connector()
    assert client().patch(f"/api/collector/connectors/{cid}", json={"config": {"token": "apify-plain"}}).status_code == 200
    with SessionLocal() as db:
        assert db.get(Connector, cid).config["token"] == "apify-plain"
        from app.collector.kpis import collector_kpis
        assert collector_kpis(db, 24)["totals"]["credentials_unencrypted"] == 1


def test_with_a_key_secrets_are_sealed_at_rest_and_opened_only_for_the_adapter(clean_catalog, monkeypatch):
    monkeypatch.setenv("CREDENTIALS_KEY", Fernet.generate_key().decode())
    cid = apify_connector()
    c = client()
    assert c.patch(f"/api/collector/connectors/{cid}", json={"config": {"token": "apify-secret", "actor": "a/b"}}).status_code == 200
    with SessionLocal() as db:
        cfg = db.get(Connector, cid).config
    assert cfg["token"].startswith("enc:v1:") and "apify-secret" not in json.dumps(cfg) and cfg["actor"] == "a/b"
    assert ConnectorFactory.get("apify_meta", cfg).config["token"] == "apify-secret"
    nested = {"apify_fallback": {"token": secrets_box.seal("fallback-secret")}}
    assert secrets_box.unseal_config(nested)["apify_fallback"]["token"] == "fallback-secret"
    assert c.patch(f"/api/collector/connectors/{cid}", json={"config": {"token": "••••"}}).status_code == 200  # masked echo keeps it
    with SessionLocal() as db:
        assert secrets_box.unseal(db.get(Connector, cid).config["token"]) == "apify-secret"


def test_a_sealed_secret_without_the_key_is_a_clear_configuration_error(clean_catalog, monkeypatch):
    monkeypatch.setenv("CREDENTIALS_KEY", Fernet.generate_key().decode())
    sealed = secrets_box.seal("x")
    monkeypatch.delenv("CREDENTIALS_KEY")
    from app.collector.base import NotConfigured
    with pytest.raises(NotConfigured, match="CREDENTIALS_KEY"):
        ConnectorFactory.get("apify_meta", {"token": sealed})


def test_existing_plaintext_secrets_are_sealed_by_the_cli(clean_catalog, monkeypatch):
    monkeypatch.delenv("CREDENTIALS_KEY", raising=False)
    cid = apify_connector()
    with SessionLocal() as db:
        db.get(Connector, cid).config = {"token": "old-plain", "actor": "a/b"}
        db.commit()
    monkeypatch.setenv("CREDENTIALS_KEY", Fernet.generate_key().decode())
    assert secrets_box.seal_existing() == {"connectors": 1, "fields": 1}
    assert secrets_box.seal_existing() == {"connectors": 0, "fields": 0}  # idempotent
    with SessionLocal() as db:
        cfg = db.get(Connector, cid).config
    assert cfg["token"].startswith("enc:v1:") and secrets_box.unseal(cfg["token"]) == "old-plain"


# ---------------------------------------------------------------- per-market rate budget
def test_each_market_has_its_own_rate_budget_shared_across_crawls(monkeypatch):
    taken = []
    a, b = MetaLibraryConnector({}), MetaLibraryConnector({})
    assert a.market_bucket("US") is b.market_bucket("us") and a.market_bucket("US") is not a.market_bucket("PH")
    for c in (a, b):
        monkeypatch.setattr(c, "authenticate", lambda: None)
        monkeypatch.setattr(c.bucket, "take", lambda: taken.append("global"))
        c._col = SimpleNamespace(client=SimpleNamespace(search_ads=lambda **kw: ({"ads": [raw(1)], "page_info": {"has_next_page": False}}, None)))
    monkeypatch.setattr(a.market_bucket("US"), "take", lambda: taken.append("US"))
    monkeypatch.setattr(a.market_bucket("PH"), "take", lambda: taken.append("PH"))
    from app.collector.base import FetchParams
    a.fetch_page(FetchParams(), "US", {}), b.fetch_page(FetchParams(), "PH", {})
    assert taken == ["global", "US", "global", "PH"]


# ---------------------------------------------------------------- operations KPIs + silent-zero alert
def test_ops_kpis(clean_catalog, monkeypatch):
    now = datetime.utcnow()
    with SessionLocal() as db:
        c = Connector(name="Meta", provider="meta", kind="browser", group="transparency", adapter="meta_library", enabled=True, config={},
                      health="healthy", last_sync_at=now - timedelta(minutes=90))
        db.add(c)
        db.flush()
        db.add(IngestBatch(key="extension:k", payload_hash="h", result={}, source="extension", outbox_backlog=12, created_at=now))
        v2 = lambda h: {"algorithm": "cellmean-dhash", "version": 2, "frame_hashes": [h, h, h]}
        db.add_all([Creative(type="video", source_url="https://v.example/1", family_id=5, video_fp="0f0f0f0f0f0f0f0f:0f0f0f0f0f0f0f0f:0f0f0f0f0f0f0f0f",
                             video_fp_v2=v2("00000000000000ff")),
                    Creative(type="video", source_url="https://v.example/2", family_id=5, video_fp="f0f0f0f0f0f0f0f0:f0f0f0f0f0f0f0f0:f0f0f0f0f0f0f0f0",
                             video_fp_v2=v2("00000000000000ff"))])
        db.commit()
        cid = c.id
    key = "c:%d:" % cid
    t = cq.claim(key, "c", cid)
    cq.finish(key, t, {"pages": 4, "fetched": 40, "new_ads": 10, "updated_ads": 20})
    from app.collector.kpis import collector_kpis
    with SessionLocal() as db:
        k = collector_kpis(db, 24)
    row = next(r for r in k["connectors"] if r["id"] == cid)
    assert 89 <= row["freshness_min"] <= 91
    assert k["totals"]["ad_yield"] == 7.5 and k["totals"]["duplicate_ratio"] == 0.5     # 30 accepted / 4 pages · 20 dup / 40
    assert k["totals"]["outbox_backlog"] == 12 and k["totals"]["fp_mismatch_rate"] == 1.0  # v1 says different, v2 says same


def test_a_source_that_silently_returns_zero_raises_an_alert(clean_catalog, monkeypatch):
    class Empty:
        def fetch_ads(self, params):
            return iter(())
    monkeypatch.setattr(realtime.ConnectorFactory, "get", lambda *a: Empty())
    with SessionLocal() as db:
        c = Connector(name="Meta", provider="meta", kind="browser", group="transparency", adapter="meta_library", enabled=True,
                      config={"keywords": "blender"})
        db.add(c)
        db.commit()
        cid = c.id
    realtime.run_connector(cid)
    with SessionLocal() as db:
        assert db.get(Connector, cid).health == "degraded"
        assert db.scalar(select(func.count()).select_from(Event).where(Event.type == "SOURCE_ZERO_RESULTS")) == 1


def test_a_retry_with_a_new_backlog_count_is_the_same_batch(clean_catalog, monkeypatch):
    """The extension reports its outbox backlog on every send; it changes between retries and must not look like a new payload."""
    monkeypatch.setattr("app.media.enqueue", lambda *a: None)
    body = {"items": [raw(1)], "idempotency_key": "bk", "country": "US"}
    c = client()
    first = c.post("/api/ingest/meta-library", json={**body, "outbox_backlog": 5})
    retry = c.post("/api/ingest/meta-library", json={**body, "outbox_backlog": 4})
    assert first.status_code == retry.status_code == 200 and retry.json()["new_ads"] == 1
    with SessionLocal() as db:
        assert db.get(IngestBatch, "extension:bk").outbox_backlog == 5
