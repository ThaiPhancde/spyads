"""Phase 1 hardening after the 2026-11-03 gap audit (docs/META_RELIABILITY_GAP_AUDIT.md): cursor cycles, a page budget for
filtered pages, keeping the pages a crawl already fetched when a later page fails, a crash between commit and finish, and an
extension retry that races its own first request (API timeout)."""
import threading
import time
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app import crawl_queue as cq, ingest, realtime
from app.collector.adapters.meta import MetaLibraryConnector, map_library_ad
from app.collector.base import FetchParams, RateLimited
from app.crawl_queue import CrawlJob, CrawlRun
from app.db import SessionLocal
from app.models import Ad, AdSource, Connector, IngestBatch, TrackedQuery


@pytest.fixture(autouse=True)
def isolated(clean_catalog, monkeypatch):
    monkeypatch.setattr(ingest, "write_raw_batch", lambda *a: "test/raw.json")
    monkeypatch.setattr("app.media.enqueue", lambda *a: None)
    monkeypatch.setattr("app.services.engine.rescore_products", lambda *a: 0)


def raw(i, fmt="VIDEO"):
    return {"ad_archive_id": str(i), "page_id": "p", "collation_id": f"g{i}",
            "snapshot": {"title": "Portable electric blender", "body": {"text": "Buy blender now"}, "display_format": fmt,
                         "videos": [{"video_sd_url": f"https://video.example/{i}.mp4"}] if fmt == "VIDEO" else [],
                         "images": [] if fmt == "VIDEO" else [{"original_image_url": f"https://img.example/{i}.jpg"}]}}


def rec(i):
    return map_library_ad(raw(i), "meta_library", "US")


def ad_ids():
    with SessionLocal() as db:
        return sorted(db.scalars(select(Ad.external_id)))


def source(monkeypatch, pages):
    """A Meta connector whose GraphQL client serves `pages`: {cursor_in: (ads, cursor_out, has_next)}."""
    calls = []

    def search_ads(**kw):
        calls.append(kw["cursor"])
        ads, nxt, more = pages[kw["cursor"]]
        return {"ads": ads, "page_info": {"has_next_page": more}}, nxt
    c = MetaLibraryConnector({})
    monkeypatch.setattr(c, "authenticate", lambda: None)
    monkeypatch.setattr(c.bucket, "take", lambda: None)
    c._col = SimpleNamespace(client=SimpleNamespace(search_ads=search_ads))
    return c, calls


# ---------------------------------------------------------------- cursor cycle A → B → A
def test_cursor_cycle_is_detected_and_pauses_without_claiming_the_end(monkeypatch):
    """Meta handed back an earlier cursor: following it repeats the same pages for ever (live 'load more' and scheduled resume)."""
    c, calls = source(monkeypatch, {None: ([raw(1)], "A", True), "A": ([raw(2)], "B", True), "B": ([raw(3)], "A", True)})
    p = FetchParams(countries=["US"], limit=1000)
    got = [r.source_ad_id for r in c.fetch_ads(p)]
    assert got == ["1", "2", "3"] and calls == [None, "A", "B"]          # the cycling page's own ads are kept, then it stops
    out = p.extra["resume_out"]["US"]
    assert out["done"] is False and out["cursor"] is None                 # never "the end"; the next run starts over
    assert c.cursor_cycles == 1


def test_live_search_page_loop_stops_on_a_cycle(monkeypatch):
    c, calls = source(monkeypatch, {None: ([raw(1)], "A", True), "A": ([raw(2)], "B", True), "B": ([raw(3)], "A", True)})
    st, n = {}, 0
    while n < 10 and not st.get("done"):
        recs, st = c.fetch_page(FetchParams(), "US", st)
        n += 1
        if st.get("page_status") in ("partial", "cycle"):
            break
    assert st["page_status"] == "cycle" and n == 3 and st["cursor"] is None


def test_an_immediately_repeated_cursor_is_still_a_source_error(monkeypatch):
    from app.collector.base import ConnectorError
    c, _ = source(monkeypatch, {None: ([raw(1)], "A", True), "A": ([raw(2)], "A", True)})
    st = c.fetch_page(FetchParams(), "US", {})[1]
    with pytest.raises(ConnectorError):
        c.fetch_page(FetchParams(), "US", st)


# ---------------------------------------------------------------- page budget
def test_pages_that_the_filter_empties_stop_at_a_page_budget(monkeypatch):
    """media_type=video but the source keeps returning image ads: got never grows and the old loop walked every page there was."""
    pages = {None: ([raw(0, "IMAGE")], "c1", True), **{f"c{i}": ([raw(i, "IMAGE")], f"c{i + 1}", True) for i in range(1, 500)}}
    c, calls = source(monkeypatch, pages)
    p = FetchParams(countries=["US"], limit=30, media_type="video")
    assert list(c.fetch_ads(p)) == []
    assert len(calls) <= 6 and p.extra["resume_out"]["US"]["done"] is False   # paused at the budget, cursor kept


# ---------------------------------------------------------------- a later page fails: earlier pages are kept
def tracked(adapter="meta_library"):
    with SessionLocal() as db:
        c = Connector(name="Meta", provider="meta", kind="browser", group="transparency", adapter=adapter, enabled=True, config={})
        db.add(c)
        db.flush()
        q = TrackedQuery(connector_id=c.id, query="blender", countries=["US"], cursor={"at": datetime.utcnow().isoformat(),
                                                                                       "pages": {"US": {"cursor": "c0"}}})
        db.add(q)
        db.commit()
        db.expunge(q)
        return c.id, q


class PagesThenFail:
    """Pages 1-2 arrive (cursor c1, c2), page 3 is rate limited — like a Meta request timing out mid-crawl."""
    def fetch_ads(self, params):
        assert params.extra["resume"]["US"]["cursor"] == "c0"
        out = params.extra.setdefault("resume_out", {})
        for page, ids in ((1, (1, 2)), (2, (3,))):
            out["US"] = {"cursor": f"c{page}", "done": False}
            for i in ids:
                yield rec(i)
        raise RateLimited("Meta Ad Library rate limit")


def test_later_page_failure_keeps_the_pages_already_fetched(monkeypatch):
    monkeypatch.setattr(realtime.ConnectorFactory, "get", lambda *a: PagesThenFail())
    cid, q = tracked()
    r = realtime.run_connector(cid, query=q)
    assert r["error"] and r["health"] == "rate_limited" and r["partial"] and r["fetched"] == 3
    assert ad_ids() == ["1", "2", "3"]                                    # used to be [] — every later retry failed the same way
    with SessionLocal() as db:
        assert db.get(TrackedQuery, q.id).cursor["pages"]["US"]["cursor"] == "c2"   # resumes after the last whole page
        assert db.get(Connector, cid).health == "rate_limited"


def test_partial_run_is_recorded_with_its_counts_and_checkpoint(monkeypatch):
    monkeypatch.setattr(realtime.ConnectorFactory, "get", lambda *a: PagesThenFail())
    cid, q = tracked()
    key = realtime.job_key(cid, q)
    realtime._job_wrapper(key, cid, q, realtime.claim_crawl(cid, q))
    with SessionLocal() as db:
        j = db.get(CrawlJob, key)
        run = db.scalar(select(CrawlRun).where(CrawlRun.job_key == key))
        assert j.status == "retry_wait" and j.checkpoint["pages"]["US"]["cursor"] == "c2"
        assert (run.status, run.fetched, run.new_ads) == ("error", 3, 3) and run.checkpoint_after["pages"]["US"]["cursor"] == "c2"


def test_first_page_failure_still_stores_nothing(monkeypatch):
    class FailFirst:
        def fetch_ads(self, params):
            raise RateLimited("Meta Ad Library rate limit")
            yield  # noqa
    monkeypatch.setattr(realtime.ConnectorFactory, "get", lambda *a: FailFirst())
    cid, q = tracked()
    r = realtime.run_connector(cid, query=q)
    assert r["error"] and not r.get("partial") and ad_ids() == []
    with SessionLocal() as db:
        assert db.get(TrackedQuery, q.id).cursor["pages"]["US"]["cursor"] == "c0"


# ---------------------------------------------------------------- worker crash between commit and finish
def test_crash_after_commit_before_finish_resumes_from_the_committed_cursor(monkeypatch):
    seen = []

    class Resumable:
        def fetch_ads(self, params):
            start = params.extra["resume"]["US"]["cursor"]
            seen.append(start)
            out = params.extra.setdefault("resume_out", {})
            nxt = {"c0": ("c1", (1, 2)), "c1": ("c2", (2, 3))}[start]        # ad 2 comes back on the next page too
            out["US"] = {"cursor": nxt[0], "done": False}
            for i in nxt[1]:
                yield rec(i)
    monkeypatch.setattr(realtime.ConnectorFactory, "get", lambda *a: Resumable())
    cid, q = tracked()
    key = realtime.job_key(cid, q)
    t0 = datetime.utcnow()
    token = cq.claim(key, "q", cid, q.id, worker="w1", now=t0)
    realtime.run_connector(cid, query=q, lease=(key, token))             # data + cursor committed, then w1 dies: no finish()
    later = t0 + timedelta(seconds=cq.LEASE_S + 1)
    assert cq.recover(later) == 1
    with SessionLocal.begin() as db:
        db.get(CrawlJob, key).next_retry_at = later
    token2 = cq.claim(key, "q", cid, q.id, worker="w2", now=later)
    with SessionLocal() as db:
        q2 = db.get(TrackedQuery, q.id)
        db.expunge(q2)
    realtime.run_connector(cid, query=q2, lease=(key, token2))
    assert seen == ["c0", "c1"] and ad_ids() == ["1", "2", "3"]          # no page lost, no ad stored twice
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(AdSource)) == 3


# ---------------------------------------------------------------- extension retry racing its own first request (API timeout)
def test_retry_while_the_first_request_is_still_running_stores_once(monkeypatch):
    """The extension gives up after 30 s and resends the same key while the server is still writing the first copy."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import app.collector.batches as B
    from app.routers.intel import require_token, router
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_token] = lambda: None
    original, entered = B.ingest_ads, threading.Event()

    def slow(*a, **k):
        entered.set()
        time.sleep(0.5)
        return original(*a, **k)
    monkeypatch.setattr(B, "ingest_ads", slow)
    body = {"items": [raw(1), raw(2)], "idempotency_key": "slow-key", "country": "US"}
    out = {}

    def send(name):
        out[name] = TestClient(app).post("/api/ingest/meta-library", json=body)
    first = threading.Thread(target=send, args=("first",))
    first.start()
    entered.wait(5)
    send("retry")
    first.join(10)
    assert out["first"].status_code == 200 and out["retry"].status_code == 200
    assert out["first"].json()["new_ads"] == out["retry"].json()["new_ads"] == 2   # the retry gets the stored receipt
    assert ad_ids() == ["1", "2"]
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(IngestBatch)) == 1
