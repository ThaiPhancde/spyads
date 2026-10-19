"""Account isolation, durable scheduling and evidence-based tracking. Uses conftest's disposable database only."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db import Base, SessionLocal, engine
from app.main import app
from app.collector.contract import AdRecord, MediaItem
from app.models import Ad, Advertiser, Creative, Product, SearchJob
from app.workspace.accounts import issue_reset
from app.workspace.auth import digest, hash_password, rate_limit
from app.workspace.identity import resolve
from app.workspace.migrate import migrate
from app.workspace.models import (ActivityEvent, AdInteraction, AdSnapshot, AuthRateLimit, Follow, Notification,
                                   Profile, ScanJob, SourceQuota, TrackedAd, User, UserSession)
from app.workspace.sources import ScanResult
from app.workspace.tracking import apply_scan
from app.workspace.worker import claim_due, fail, run_scan

PASSWORD = "ToolSpy test password 123!"


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    assert engine.dialect.name == "sqlite" or engine.url.database.startswith("toolspy_test_"), "Only a disposable test database can be reset"
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "0")
    monkeypatch.setenv("SMTP_HOST", "")
    monkeypatch.setenv("COMPETITOR_CONCURRENCY", "1")
    monkeypatch.setenv("COMPETITOR_SCANS_PER_SOURCE_HOUR", "10")


def create_user(email="mkt-a@example.test", role="MKT", status="active"):
    with SessionLocal.begin() as db:
        u = User(email=email, name=email.split("@")[0], role=role, status=status, password_hash=hash_password(PASSWORD))
        db.add(u); db.flush(); db.add(Profile(user_id=u.id)); return u.id


def client(email=None):
    c = TestClient(app)
    assert c.get("/api/auth/csrf").status_code == 200
    if email:
        r = write(c, "POST", "/auth/login", {"email": email, "password": PASSWORD})
        assert r.status_code == 200, r.text
    return c


def write(c, method, path, body=None, **kwargs):
    return c.request(method, "/api" + path, headers={"X-CSRF-Token": c.cookies.get("toolspy_csrf", "")}, json=body, **kwargs)


def seed_advertisers(n=4):
    ids = []
    with SessionLocal.begin() as db:
        for i in range(n):
            adv = Advertiser(name=f"Advertiser {i}", source="meta_library", source_advertiser_id=str(100 + i), platform="facebook", country="US")
            db.add(adv); db.flush()
            p = Product(product_code=f"T{i}", canonical_name="lucky bracelet", country="US", category="accessories", name_status="VERIFIED")
            db.add(p); db.flush()
            ad = Ad(external_id=f"ad-{i}", source="meta_library", platform="facebook", advertiser_id=adv.id, product_id=p.id,
                    title="lucky bracelet", ad_text="lucky bracelet", search_text="lucky bracelet", country="US", page_id=adv.source_advertiser_id,
                    channel="ads", network="meta", is_active=True)
            db.add(ad); db.flush(); ids.append((adv.id, ad.id, p.id))
    return ids


def follow(c, aid, hours=24):
    r = write(c, "POST", f"/competitors/{aid}/follow", {"interval_hours": hours})
    assert r.status_code == 200, r.text


def test_registration_is_active_at_once_login_logout_and_internal_lock(monkeypatch):
    anon = client()
    assert write(anon, "POST", "/auth/register", {"name": "A", "email": "new@example.test", "password": PASSWORD, "role": "ADMIN"}).status_code == 422
    assert write(anon, "POST", "/auth/register", {"name": "A", "email": "new@example.test", "password": PASSWORD}).status_code == 201
    with SessionLocal() as db:
        u = db.scalar(select(User).where(User.email == "new@example.test")); uid = u.id
        assert u.role == "MKT" and u.status == "active" and u.password_hash.startswith("$argon2id$")
    mkt = client("new@example.test")  # no approval step: log in right after registering
    cookie = mkt.cookies.get("toolspy_session")
    with SessionLocal() as db:
        assert db.get(UserSession, digest(cookie)) and not db.get(UserSession, cookie)
    assert write(mkt, "POST", "/auth/logout").status_code == 200
    assert mkt.get("/api/auth/me").status_code == 401
    mkt = client("new@example.test")
    from app.workspace import cli  # suspension is an internal operation (no ADMIN account exists)
    monkeypatch.setattr("sys.argv", ["cli", "lock", "new@example.test"])
    cli.main()
    assert mkt.get("/api/auth/me").status_code == 401
    monkeypatch.setattr("sys.argv", ["cli", "unlock", "new@example.test"])
    cli.main()
    assert client("new@example.test").get("/api/auth/me").status_code == 200


def test_csrf_permissions_and_secure_cookie(monkeypatch):
    uid = create_user(); c = client("mkt-a@example.test")
    assert c.post("/api/auth/logout").status_code == 403
    assert c.post("/api/auth/logout", headers={"X-CSRF-Token": c.cookies.get("toolspy_csrf"), "Origin": "https://attacker.invalid"}).status_code == 403
    assert write(c, "PATCH", f"/admin/users/{uid}", {"status": "active"}).status_code == 403
    assert write(c, "DELETE", "/collector/connectors/1").status_code == 403
    assert c.get("/api/admin/users").status_code == 403
    assert c.get("/api/admin/reset-requests").status_code == 403
    assert TestClient(app).get("/api/ads").status_code == 401
    assert TestClient(app).get("/api/health").status_code == 200
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "1")
    https = TestClient(app, base_url="https://localhost")
    https.get("/api/auth/csrf")
    r = write(https, "POST", "/auth/login", {"email": "mkt-a@example.test", "password": PASSWORD})
    assert r.status_code == 200
    assert "HttpOnly" in r.headers["set-cookie"] and "Secure" in r.headers["set-cookie"] and "SameSite=lax" in r.headers["set-cookie"]


def test_password_reset_one_use_and_change_revoke_sessions():
    uid = create_user(); c1 = client("mkt-a@example.test"); c2 = client("mkt-a@example.test")
    new = PASSWORD + "new"
    assert write(c1, "POST", "/auth/password", {"current_password": PASSWORD, "new_password": new}).status_code == 200
    assert c2.get("/api/auth/me").status_code == 401
    assert c1.get("/api/auth/me").status_code == 200
    with SessionLocal.begin() as db:
        otp = issue_reset(db, db.get(User, uid))
    anon = client()
    body = {"email": "mkt-a@example.test", "otp": otp, "password": PASSWORD}
    assert write(anon, "POST", "/auth/reset-password", body).status_code == 200
    assert write(anon, "POST", "/auth/reset-password", body).status_code == 400  # one use
    assert c1.get("/api/auth/me").status_code == 401


def test_private_follows_persist_and_shared_job():
    create_user(); create_user("mkt-b@example.test"); a = client("mkt-a@example.test"); b = client("mkt-b@example.test")
    ids = seed_advertisers()
    for aid, _, _ in ids[:2]: follow(a, aid)
    for aid, _, _ in ids[2:]: follow(b, aid)
    follow(b, ids[0][0], 6); follow(a, ids[0][0])
    assert a.get("/api/competitors").json()["total"] == 2
    assert b.get("/api/competitors").json()["total"] == 3
    assert b.get(f"/api/competitors/{ids[1][0]}/timeline").status_code == 404
    assert write(a, "POST", "/auth/logout").status_code == 200
    a = client("mkt-a@example.test")
    assert {x["id"] for x in a.get("/api/competitors").json()["rows"]} == {x[0] for x in ids[:2]}
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(ScanJob)) == 4
        assert db.scalar(select(func.count()).select_from(TrackedAd)) == 4
    assert write(a, "DELETE", f"/competitors/{ids[0][0]}/follow").status_code == 200
    assert b.get("/api/competitors").json()["total"] == 3


def test_private_library_searches_notifications_and_jobs():
    ua = create_user(); ub = create_user("mkt-b@example.test"); a = client("mkt-a@example.test"); b = client("mkt-b@example.test")
    aid, adid, pid = seed_advertisers(1)[0]
    assert write(a, "PUT", f"/workspace/ads/{adid}", {"saved": True, "favorite": True, "tested": True, "note": "private"}).status_code == 200
    assert b.get("/api/workspace/ads").json()["total"] == 0
    assert write(b, "PUT", f"/workspace/ads/{adid}", {"saved": True, "user_id": ua}).status_code == 422
    sid = write(a, "POST", "/workspace/searches", {"name": "private search", "filters": {"q": "bracelet", "has_video": True, "expand": False, "min_days": 7, "funnel": "", "user_id": ub}}).json()["id"]
    assert a.get("/api/workspace/searches").json()["rows"][0]["filters"] == {"q": "bracelet", "has_video": "true", "expand": "false", "min_days": "7", "funnel": ""}
    assert write(b, "DELETE", f"/workspace/searches/{sid}").status_code == 404
    with SessionLocal.begin() as db:
        j = SearchJob(query="private", user_id=ua, status="done"); db.add(j); db.flush(); jid = j.id
    assert b.get(f"/api/search/jobs/{jid}").status_code == 404
    assert write(b, "POST", f"/search/jobs/{jid}/more", {"count": 10}).status_code == 404
    assert b.get("/api/search/jobs").json() == []
    assert "private" not in b.get("/api/workspace/activity").text


def test_new_ads_once_snapshots_changes_and_unknown():
    uid = create_user(); create_user("mkt-b@example.test"); a = client("mkt-a@example.test"); b = client("mkt-b@example.test")
    aid, _, _ = seed_advertisers(1)[0]; follow(a, aid); follow(b, aid)
    rec = AdRecord(source="meta_library", source_ad_id="new", page_id="100", advertiser="A", active=True,
                   ad_text="bracelet", landing_page="https://example.test/one", likes=12, media=[MediaItem("image", "https://cdn.test/a.jpg?sig=one")])
    with SessionLocal.begin() as db:
        adv = db.get(Advertiser, aid)
        apply_scan(db, adv, ScanResult([rec]), "run1")
    with SessionLocal.begin() as db:
        adv = db.get(Advertiser, aid)
        apply_scan(db, adv, ScanResult([rec]), "run1")
        assert db.scalar(select(func.count()).select_from(ActivityEvent).where(ActivityEvent.kind == "new_ad")) == 1
        assert db.scalar(select(func.count()).select_from(Notification)) == 2
    rec.media[0].url = "https://cdn.test/a.jpg?sig=two"
    with SessionLocal.begin() as db:
        assert apply_scan(db, db.get(Advertiser, aid), ScanResult([rec]), "run2") == 0
    rec.landing_page = "https://example.test/two"
    with SessionLocal.begin() as db:
        assert apply_scan(db, db.get(Advertiser, aid), ScanResult([rec]), "run3") == 1
    with SessionLocal.begin() as db:
        apply_scan(db, db.get(Advertiser, aid), ScanResult([]), "run4")
        row = db.scalar(select(TrackedAd).where(TrackedAd.source_ad_id == "new"))
        assert row.status == "unknown"
        assert db.scalar(select(func.count()).select_from(ActivityEvent).where(ActivityEvent.kind == "ad_inactive")) == 0
    rec.active = False
    with SessionLocal.begin() as db:
        apply_scan(db, db.get(Advertiser, aid), ScanResult([rec]), "run5")
    na = a.get("/api/workspace/notifications").json()["rows"]; nb = b.get("/api/workspace/notifications").json()["rows"]
    assert write(b, "PATCH", f"/workspace/notifications/{na[0]['id']}", {"read": True}).status_code == 404
    assert write(a, "PATCH", f"/workspace/notifications/{na[0]['id']}", {"read": True}).status_code == 200
    assert not b.get("/api/workspace/notifications").json()["rows"][0]["read"]
    timeline = a.get(f"/api/competitors/{aid}/timeline").json()["rows"]
    assert timeline[-1]["kind"] == "follow"
    dates = [e["at"] for e in timeline]
    assert dates == sorted(dates, reverse=True)
    assert a.get("/api/workspace/dashboard").json()["new_ads_24h"] == 1


def test_schedule_without_sessions_restart_and_concurrency(monkeypatch):
    create_user(); c = client("mkt-a@example.test"); aid, _, _ = seed_advertisers(1)[0]; follow(c, aid)
    write(c, "POST", "/auth/logout")
    with ThreadPoolExecutor(4) as pool:
        claims = list(pool.map(lambda _: claim_due(), range(4)))
    assert len([c for c in claims if c]) == 1
    first = next(c for c in claims if c)
    with SessionLocal.begin() as db:
        j = db.get(ScanJob, aid); j.lease_until = datetime.utcnow() - timedelta(seconds=1)
    second = claim_due()
    assert second and second[1] != first[1]
    fail(*first, "old worker")  # stale lease cannot override the new worker
    with SessionLocal() as db:
        assert db.get(ScanJob, aid).run_token == second[1]


def test_backoff_quota_and_data_survive_source_failure(monkeypatch):
    create_user(); c = client("mkt-a@example.test"); aid, _, _ = seed_advertisers(1)[0]; follow(c, aid)
    token = claim_due(); assert token
    before = datetime.utcnow()
    fail(*token, "source failure")
    with SessionLocal() as db:
        job = db.get(ScanJob, aid)
        assert job.next_check_at >= before + timedelta(minutes=2)
        assert job.last_success_at is None and job.failures == 1
        assert db.scalar(select(func.count()).select_from(TrackedAd)) == 1
        assert db.scalar(select(func.count()).select_from(AdSnapshot)) == 1
    assert claim_due() is None
    monkeypatch.setenv("COMPETITOR_SCANS_PER_SOURCE_HOUR", "1")
    with SessionLocal.begin() as db:
        db.get(ScanJob, aid).next_check_at = datetime.utcnow() - timedelta(seconds=1)
    assert claim_due() is None
    with SessionLocal() as db:
        assert db.get(ScanJob, aid).source_status == "quota_wait"


def test_worker_success_and_unsupported(monkeypatch):
    create_user(); c = client("mkt-a@example.test"); aid, _, _ = seed_advertisers(1)[0]; follow(c, aid, 6)
    monkeypatch.setattr("app.workspace.worker.fetch", lambda db, adv: ScanResult([]))
    token = claim_due(); run_scan(*token)
    with SessionLocal() as db:
        job = db.get(ScanJob, aid)
        assert job.source_status == "success" and job.last_success_at
        assert 5.9 < (job.next_check_at - datetime.utcnow()).total_seconds() / 3600 < 6.1
    assert c.get("/api/competitors").json()["rows"][0]["last_changes"] == 0
    monkeypatch.undo()
    with SessionLocal.begin() as db:
        adv = db.get(Advertiser, aid); adv.source = "tiktok_top_ads"
        job = db.get(ScanJob, aid); job.source = adv.source; job.next_check_at = datetime.utcnow()
    token = claim_due(); run_scan(*token)
    with SessionLocal() as db:
        assert db.get(ScanJob, aid).source_status == "unsupported"
        assert db.scalar(select(func.count()).select_from(TrackedAd)) == 1


def test_whitelist_blacklist_search_library_and_no_name_bypass(monkeypatch):
    create_user(); c = client("mkt-a@example.test"); ids = seed_advertisers()
    for aid, _, _ in ids[:2]: follow(c, aid)
    for aid, _, _ in ids[2:]: assert write(c, "PUT", f"/workspace/filters/{aid}", {"mode": "blacklist"}).status_code == 200
    for aid, _, _ in ids[:2]: assert write(c, "PUT", f"/workspace/filters/{aid}", {"mode": "whitelist"}).status_code == 200
    assert write(c, "PUT", "/workspace/filters", {"mode": "whitelist"}).status_code == 200
    ads = c.get("/api/ads?active=true&country=US").json()
    assert {r["advertiser_id"] for r in ads["rows"]} == {i[0] for i in ids[:2]}
    r = c.get("/api/search?q=lucky%20bracelet&country=US&expand=false"); assert r.status_code == 200, r.text
    assert {p["id"] for p in r.json()["rows"]} == {i[2] for i in ids[:2]}
    assert r.json()["total"] == 2
    # A caller supplying forbidden product ids must not bypass filters through the no-keyword path.
    r = c.get(f"/api/search?ids={ids[2][2]}&expand=false")
    assert r.json()["rows"] == []
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Ad)) == 4


def test_identity_same_name_not_merged_and_migration_preserves_ads():
    with SessionLocal.begin() as db:
        a = resolve(db, AdRecord(source="meta_library", source_ad_id="one", page_id="100", advertiser="Same Name"))
        b = resolve(db, AdRecord(source="meta_library", source_ad_id="two", page_id="101", advertiser="Same Name"))
        assert a.id != b.id
        old = Advertiser(name="Legacy name", platform="facebook"); db.add(old); db.flush()
        for i in range(2): db.add(Ad(external_id=f"legacy{i}", source="meta_library", platform="facebook", page_id=str(200+i), advertiser_id=old.id))
        db.add(Ad(external_id="unverified-legacy", source="other_provider", platform="facebook", advertiser_id=old.id))
    migrate(); migrate()
    with SessionLocal() as db:
        ads = db.scalars(select(Ad).order_by(Ad.id)).all()
        assert len(ads) == 3 and len({a.advertiser_id for a in ads}) == 3
        assert db.get(Advertiser, ads[2].advertiser_id).source_advertiser_id.startswith("unverified:")


def test_rate_limit_atomic_across_threads():
    def attempt(_):
        from fastapi import HTTPException
        try:
            rate_limit("concurrent-test", limit=3); return True
        except HTTPException as e:
            assert e.status_code == 429; return False
    with ThreadPoolExecutor(6) as pool:
        assert sum(pool.map(attempt, range(10))) == 3


def test_retry_is_bounded_and_prolonged_error_notification_is_once():
    create_user(); c = client("mkt-a@example.test"); aid, _, _ = seed_advertisers(1)[0]; follow(c, aid)
    now = datetime.utcnow()
    for attempt in range(3):
        token = claim_due(now)
        assert token
        fail(*token, "source unavailable", now=now)
        with SessionLocal() as db:
            job = db.get(ScanJob, aid)
            if attempt < 2:
                assert job.attempts == attempt + 1
                assert job.next_check_at == now + timedelta(minutes=2 ** (attempt + 1))
            else:
                assert job.attempts == 0 and job.next_check_at == now + timedelta(hours=24)
            now = job.next_check_at
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Notification)) == 1
        assert db.scalar(select(func.count()).select_from(AdSnapshot)) == 1


def test_process_timeout_really_terminates_crawler(monkeypatch):
    import asyncio
    import subprocess
    import sys
    from app.workspace import worker
    create_user(); c = client("mkt-a@example.test"); aid, _, _ = seed_advertisers(1)[0]; follow(c, aid)
    token = claim_due()
    original = subprocess.Popen
    children = []
    def slow_source(*args, **kwargs):
        proc = original([sys.executable, "-c", "import time; time.sleep(30)"], **kwargs)
        children.append(proc)
        return proc
    monkeypatch.setattr(worker.subprocess, "Popen", slow_source)
    monkeypatch.setattr(worker, "timeout_seconds", lambda: 1)
    asyncio.run(worker.execute(*token))
    assert children[0].poll() is not None
    with SessionLocal() as db:
        job = db.get(ScanJob, aid)
        assert job.source_status == "timeout" and job.lease_until is None
        assert db.scalar(select(func.count()).select_from(TrackedAd)) == 1


def test_tiktok_recency_is_not_activity_evidence():
    from app.collector.adapters.ad_libraries import map_tiktok_library
    old = {"id": "123", "last_shown_date": 1600000000000}
    assert map_tiktok_library(old, {}, [], "").active is None
    assert map_tiktok_library(dict(old, status="inactive"), {}, [], "").active is False


def test_followed_advertiser_is_not_scanned_by_legacy_liveness(monkeypatch):
    from app import realtime
    from app.models import AdSource, Connector
    create_user(); c = client("mkt-a@example.test"); aid, adid, _ = seed_advertisers(1)[0]; follow(c, aid)
    with SessionLocal.begin() as db:
        db.add(Connector(name="test-meta", provider="meta", adapter="meta_library", kind="browser", group="transparency", enabled=True))
        db.add(AdSource(ad_id=adid, source="meta_library", source_ad_id="ad-0"))
    def forbidden(*args, **kwargs):
        raise AssertionError("Followed advertiser was scanned by legacy liveness")
    monkeypatch.setattr("app.collector.adapters.meta.MetaLibraryConnector.fetch_page", forbidden)
    assert realtime.verify_liveness()["advertisers"] == 0
