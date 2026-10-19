"""Engagement fallback: Ad ID → Post ID mapping, Bright Data Facebook / TikTok Posts parsing, N/A (never 0), history, budget, API.
Throw-away SQLite (conftest); Bright Data is a fake transport — no request leaves the machine."""
from datetime import datetime

import uuid

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text

from app import ingest, media
from app.collector.contract import AdRecord, MediaItem
from app.db import SessionLocal
from app.engagement import brightdata_posts as bd
from app.engagement import jobs, mapping, service
from app.engagement.models import AdEngagement, EngagementJob, PageFetch, PagePost
from app.main import app
from app.models import Ad, Creative, Product
from app.visual.models import ExternalApiCall

TT_URL = "https://www.tiktok.com/@shopper.ph/video/7506039955702644743"
TT_REC = {"post_id": "7506039955702644743", "digg_count": 1234, "comment_count": 0, "share_count": 56, "play_count": 98000, "collect_count": 7}
FB_REC = {"post_id": "1234567890", "likes": 321, "num_comments": 12, "num_shares": 4, "video_view_count": None, "num_likes_type": {"type": "Like", "num": 300}}
RUN = uuid.uuid4().hex[:6]
_n = {"i": 0}


def uniq() -> int:
    _n["i"] += 1
    return _n["i"]


@pytest.fixture
def db(monkeypatch):
    for k in ("BRIGHTDATA_ENABLED", "BRIGHTDATA_STRUCTURED_ENABLED"):
        monkeypatch.setenv(k, "true")
    monkeypatch.setenv("BRIGHTDATA_API_TOKEN", "test-token")
    monkeypatch.setenv("BRIGHTDATA_POSTS_MONTHLY_LIMIT", "30")
    monkeypatch.setenv("BRIGHTDATA_POSTS_DAILY_LIMIT", "10")
    monkeypatch.setenv("ADMIN_TOKEN", "adm")
    with SessionLocal() as s:   # unique ids per test, nothing else deleted: the SQLite file is shared with the other test modules
        s.query(ExternalApiCall).filter(ExternalApiCall.provider == "brightdata_posts").delete()
        s.query(EngagementJob).delete()
        s.query(AdEngagement).delete()   # post memory must not leak between tests
        s.query(PageFetch).delete()
        s.query(PagePost).delete()
        s.commit()
        yield s
        s.rollback()



def mk_ad(db, source="tiktok_top_ads", landing=None, likes=None, comments=None, shares=None, views=None, network="tiktok", text=None, snapshot=None) -> Ad:
    i = uniq()
    p = Product(product_code=f"EN-{i}", canonical_name="Test product", name_status="SUGGESTED")
    db.add(p)
    db.flush()
    a = Ad(external_id=f"en{RUN}-{i}", source=source, platform="tiktok", network=network, product_id=p.id, country="PH", landing_url=landing, ad_text=text, snapshot_url=snapshot,
           likes=likes, comments_count=comments, shares=shares, views=views, is_active=True, first_seen_at=datetime(2026, 10, 1))
    db.add(a)
    db.flush()
    return a


def quota_used(db, provider):
    return db.scalar(select(func.count(ExternalApiCall.id)).where(ExternalApiCall.provider == provider, ExternalApiCall.counted.is_(True)))


def fake_bd(monkeypatch, handler):
    real = httpx.Client
    monkeypatch.setattr(bd.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **{k: v for k, v in kw.items() if k != "transport"}))


# ---------------------------------------------------------------- Ad ID → Post ID
@pytest.mark.parametrize("field,url,platform,post_id", [
    ("landing_url", "https://www.tiktok.com/@shopper.ph/video/7506039955702644743?lang=en", "tiktok", "7506039955702644743"),
    ("ad_text", "watch https://tiktok.com/@a_b/video/7000000000000000001 now", "tiktok", "7000000000000000001"),
    ("landing_url", "https://www.facebook.com/lunastore/posts/pfbid02abcXYZ", "facebook", "pfbid02abcXYZ"),
    ("landing_url", "https://www.facebook.com/lunastore/videos/123456789012345/", "facebook", "123456789012345"),
    ("landing_url", "https://www.facebook.com/reel/998877665544", "facebook", "998877665544"),
    ("landing_url", "https://www.facebook.com/permalink.php?story_fbid=5550001&id=9", "facebook", "5550001"),
    ("landing_url", "https://www.facebook.com/watch/?v=4440002", "facebook", "4440002"),
])
def test_post_ref_reads_the_ids_the_ad_itself_names(field, url, platform, post_id):
    from types import SimpleNamespace

    ref = mapping.post_ref(SimpleNamespace(**{"landing_url": None, "snapshot_url": None, "ad_text": None, "title": None, "source": "x", "network": "x", field: url}))
    assert ref.get("platform") == platform and ref.get("post_id") == post_id and ref["url"].startswith("https://")


def test_ad_library_and_creative_center_ads_have_no_post_id_and_say_why():
    from types import SimpleNamespace

    meta = SimpleNamespace(landing_url="https://shop.example/p/x", snapshot_url="https://www.facebook.com/ads/library/?id=821573003929883", ad_text="Buy now",
                           title=None, source="meta_library", network="meta")
    cc = SimpleNamespace(landing_url="https://shop.example/p/x", snapshot_url="https://ads.tiktok.com/business/creativecenter/topads/7688451761497636882/pc/en",
                         ad_text=None, title=None, source="tiktok_top_ads", network="tiktok")
    assert mapping.post_ref(meta) == {"reason": "ad_library_has_no_post_id"}   # an ad-library id is NOT a post id
    assert mapping.post_ref(cc) == {"reason": "tiktok_ad_id_is_not_a_video_id"}


# ---------------------------------------------------------------- reading the Bright Data record
def test_tiktok_and_facebook_records_map_to_the_real_field_names():
    t = bd.parse("tiktok", TT_REC)
    assert (t["likes"], t["comments"], t["shares"], t["views"], t["post_id"]) == (1234, 0, 56, 98000, "7506039955702644743")   # a real 0 stays 0
    assert t["fields"] == {"likes": "digg_count", "comments": "comment_count", "shares": "share_count", "views": "play_count"}
    f = bd.parse("facebook", FB_REC)
    assert (f["likes"], f["comments"], f["shares"], f["views"]) == (321, 12, 4, None)   # video_view_count null → None, never 0


@pytest.mark.parametrize("raw,want", [(5, 5), (0, 0), ("1.2K", 1200), ("3,401", 3401), ("2M", 2_000_000), (None, None), ("", None), ("n/a", None), (-3, None), (True, None), ("null", None)])
def test_to_int_never_turns_missing_into_zero(raw, want):
    assert bd.to_int(raw) == want


# ---------------------------------------------------------------- ingest keeps unknown unknown
def test_ingest_stores_none_not_zero_and_records_history(db, monkeypatch):
    monkeypatch.setattr(ingest, "write_raw_batch", lambda *a, **k: None)
    monkeypatch.setattr(media, "enqueue", lambda *a, **k: None)

    def rec(i, **kw):
        return AdRecord(source="tiktok_top_ads", source_ad_id=f"in{RUN}-{i}", platform="tiktok", country="PH", advertiser="Shop", ad_text="Nice product for you", active=True,
                        media=[MediaItem(type="image", url=f"https://cdn.example/in{i}.jpg")], **kw)

    ingest.ingest_ads(db, [rec(1, likes=500, comments=None, shares=None), rec(2, likes=10, comments=0, shares=0), rec(3)])
    a1, a2, a3 = (db.scalar(select(Ad).where(Ad.external_id == f"in{RUN}-{i}")) for i in (1, 2, 3))
    assert (a1.likes, a1.comments_count, a1.shares, a1.views) == (500, None, None, None)
    assert (a2.likes, a2.comments_count, a2.shares) == (10, 0, 0)              # source said 0: 0
    assert (a3.likes, a3.comments_count, a3.shares) == (None, None, None)      # source said nothing: N/A
    rows = {r.ad_id: r for r in db.scalars(select(AdEngagement).where(AdEngagement.scope == "ad"))}
    assert set(rows) == {a1.id, a2.id}                                          # nothing to record for a3
    ingest.ingest_ads(db, [rec(1, likes=650, comments=3)])                      # the numbers moved: one more history row
    assert db.scalar(select(func.count(AdEngagement.id)).where(AdEngagement.ad_id == a1.id)) == 2 and a1.comments_count == 3


# ---------------------------------------------------------------- the ladder
def test_unmapped_ad_stays_na_and_says_why(db):
    a = mk_ad(db, likes=100, source="meta_library", network="meta", snapshot="https://www.facebook.com/ads/library/?id=1")
    r = service.resolve(db, a.id, allow_provider=True)
    assert r["status"] == "unmapped" and r["reason"] == "ad_library_has_no_post_id"
    s = service.summary(db, [a.id])[a.id]
    assert s["post"] is None and s["status"] == "unmapped" and s["ad"]["likes"] == 100 and s["ad"]["comments"] is None


def test_mapped_ad_waits_for_a_click_when_the_provider_is_not_automatic(db, monkeypatch):
    monkeypatch.setattr(bd, "fetch", lambda *a, **k: pytest.fail("no request without an explicit lookup"))
    a = mk_ad(db, landing=TT_URL)
    assert service.resolve(db, a.id, allow_provider=False)["status"] == "needs_provider"
    s = service.summary(db, [a.id])[a.id]
    assert s["status"] == "needs_provider" and s["post"] is None   # the card offers "Lấy tương tác"; numbers are N/A, not 0


def test_disabled_provider_is_recorded_not_faked(db, monkeypatch):
    monkeypatch.setenv("BRIGHTDATA_STRUCTURED_ENABLED", "false")
    a = mk_ad(db, landing=TT_URL)
    r = service.resolve(db, a.id, allow_provider=True)
    assert (r["status"], r["reason"]) == ("disabled", "structured_disabled")
    assert service.summary(db, [a.id])[a.id]["post"] is None


def test_lookup_reads_the_post_stores_history_and_reuses_it(db, monkeypatch):
    calls = []

    def handler(req: httpx.Request):
        calls.append(req)
        assert req.url.params["dataset_id"] == "gd_lu702nij2f790tmv9h" and req.headers["authorization"] == "Bearer test-token"
        assert req.read() == b'{"input":[{"url":"' + TT_URL.encode() + b'"}]}'
        return httpx.Response(200, json=[TT_REC])

    fake_bd(monkeypatch, handler)
    a, b = mk_ad(db, landing=TT_URL), mk_ad(db, landing=TT_URL + "?utm=x")   # two ads of the page pointing at one video
    r = service.resolve(db, a.id, allow_provider=True)
    db.commit()
    assert r["status"] == "ok" and len(calls) == 1
    post = service.summary(db, [a.id])[a.id]["post"]
    assert (post["likes"], post["comments"], post["shares"], post["views"], post["post_id"], post["platform"]) == (1234, 0, 56, 98000, "7506039955702644743", "tiktok")
    assert service.resolve(db, b.id, allow_provider=True)["status"] == "ok" and len(calls) == 1   # same post: memory, no second request
    db.commit()
    assert service.summary(db, [b.id])[b.id]["post"]["likes"] == 1234
    used = db.scalar(select(func.count(ExternalApiCall.id)).where(ExternalApiCall.provider == "brightdata_posts", ExternalApiCall.counted.is_(True)))
    assert used == 1


def test_post_and_ad_scope_are_kept_apart(db, monkeypatch):
    fake_bd(monkeypatch, lambda req: httpx.Response(200, json=[TT_REC]))
    a = mk_ad(db, landing=TT_URL, likes=40, comments=None)
    service.resolve(db, a.id, allow_provider=True)
    db.commit()
    s = service.summary(db, [a.id])[a.id]
    assert s["ad"]["likes"] == 40 and s["ad"]["comments"] is None and s["post"]["likes"] == 1234   # the ad's own 40 is not overwritten by the video's 1,234


def test_budget_is_its_own_pool_and_refuses_before_any_request(db, monkeypatch):
    monkeypatch.setenv("BRIGHTDATA_POSTS_DAILY_LIMIT", "1")
    mcp_before = quota_used(db, "brightdata")
    fake_bd(monkeypatch, lambda req: httpx.Response(200, json=[TT_REC]))
    a, b = mk_ad(db, landing=TT_URL), mk_ad(db, landing="https://www.tiktok.com/@other/video/7506039955702600000")
    assert service.resolve(db, a.id, allow_provider=True)["status"] == "ok"
    db.commit()
    r = service.resolve(db, b.id, allow_provider=True)
    assert r["status"] == "quota" and r["reason"] == "quota_exceeded_daily"
    assert service.summary(db, [b.id])[b.id]["post"] is None
    assert quota_used(db, "brightdata") == mcp_before   # the Web Scraper pool never spends the MCP pool


def test_deleted_post_is_not_found_not_zero(db, monkeypatch):
    fake_bd(monkeypatch, lambda req: httpx.Response(200, json=[]))
    a = mk_ad(db, landing=TT_URL)
    r = service.resolve(db, a.id, allow_provider=True)
    assert r["status"] == "not_found"
    s = service.summary(db, [a.id])[a.id]
    assert s["post"] is None and s["status"] == "not_found"
    assert db.scalar(select(func.count(ExternalApiCall.id)).where(ExternalApiCall.provider == "brightdata_posts", ExternalApiCall.counted.is_(True))) == 0   # no record delivered = not billed


def test_202_snapshot_is_polled_and_billed_once(db, monkeypatch):
    monkeypatch.setattr(bd.time, "sleep", lambda s: None)

    def handler(req: httpx.Request):
        p = req.url.path
        if p.endswith("/scrape"):
            return httpx.Response(202, json={"snapshot_id": "s_1"})
        if "/progress/" in p:
            return httpx.Response(200, json={"status": "ready"})
        return httpx.Response(200, json=[FB_REC])

    fake_bd(monkeypatch, handler)
    a = mk_ad(db, landing="https://www.facebook.com/lunastore/posts/1234567890", source="x", network="x")
    assert service.resolve(db, a.id, allow_provider=True)["status"] == "ok"
    db.commit()
    p = service.summary(db, [a.id])[a.id]["post"]
    assert (p["likes"], p["comments"], p["shares"], p["views"]) == (321, 12, 4, None)
    assert db.scalar(select(func.count(ExternalApiCall.id)).where(ExternalApiCall.provider == "brightdata_posts", ExternalApiCall.counted.is_(True))) == 1


# ---------------------------------------------------------------- API → queue → worker → API
def test_api_enrich_queue_worker_and_lookup(db, monkeypatch):
    fake_bd(monkeypatch, lambda req: httpx.Response(200, json=[TT_REC]))
    meta = mk_ad(db, source="meta_library", network="meta", snapshot="https://www.facebook.com/ads/library/?id=9")
    tt = mk_ad(db, landing=TT_URL, likes=70)
    db.commit()
    c = TestClient(app, headers={"X-Admin-Token": "adm"})
    r = c.post("/api/engagement/enrich", json={"ad_ids": [meta.id, tt.id]}).json()["engagement"]
    assert r[str(meta.id)]["status"] == "queued" and r[str(tt.id)]["ad"]["likes"] == 70
    assert jobs.run_pending()["ran"] == 2                       # free pass: mapping + memory, ENGAGEMENT_AUTO_ENABLED is off
    g = c.get("/api/engagement", params={"ads": f"{meta.id},{tt.id}"}).json()["engagement"]
    assert g[str(meta.id)]["status"] == "unmapped" and g[str(meta.id)]["ad"] is None and g[str(meta.id)]["post"] is None
    assert g[str(tt.id)]["status"] == "needs_provider" and g[str(tt.id)]["post"] is None
    lk = c.post("/api/engagement/lookup", json={"ad_id": tt.id}).json()                 # the explicit click
    assert lk["status"] == "ok" and lk["engagement"][str(tt.id)]["post"]["views"] == 98000
    h = c.get("/api/engagement/history", params={"ad_id": tt.id}).json()["rows"]
    assert {(r["scope"], r["status"]) for r in h} >= {("ad", "ok"), ("post", "ok"), ("post", "needs_provider")}
    assert c.get("/api/engagement/quota").json()["brightdata_posts"]["unavailable"] is None
    assert c.post("/api/engagement/lookup", json={"ad_id": 99999999}).status_code == 404


def test_ad_payload_carries_null_not_zero(db, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "engagement-regression-admin")
    a = mk_ad(db, source="meta_library", network="meta")
    db.commit()
    rows = TestClient(app, headers={"X-Admin-Token": "engagement-regression-admin"}).get("/api/ads", params={"product_id": a.product_id, "country": "ANY"}).json()["rows"]
    row = next(r for r in rows if r["id"] == a.id)
    assert row["engagement"] == {"likes": None, "comments": None, "shares": None, "views": None, "scope": "ad"}


def test_inactive_customer_is_reported_not_billed_and_not_retried(db, monkeypatch):
    """Measured live on 2026-10-14: this Bright Data account answers the Web Scraper API with 400 "Customer is not active"."""
    monkeypatch.setitem(bd._blocked, "until", 0.0)
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(400, text="Customer is not active")

    fake_bd(monkeypatch, handler)
    a, b = mk_ad(db, landing=TT_URL), mk_ad(db, landing="https://www.tiktok.com/@other/video/7506039955702600000")
    r = service.resolve(db, a.id, allow_provider=True)
    assert (r["status"], r["reason"]) == ("error", "account_not_active") and len(calls) == 1
    db.commit()
    assert quota_used(db, "brightdata_posts") == 0                       # a rejected request does not eat the budget
    r2 = service.resolve(db, b.id, allow_provider=True)                    # the block is remembered: no second request
    assert (r2["status"], r2["reason"]) == ("disabled", "account_not_active") and len(calls) == 1
    assert service.summary(db, [a.id])[a.id]["post"] is None
    monkeypatch.setitem(bd._blocked, "until", 0.0)


# ---------------------------------------------------------------- ad → post through the page's own posts (matching.py)
from app.engagement import matching  # noqa: E402

COPY = "Fix your posture in 7 days with this magnetic corrector, free shipping nationwide"
LAND = "https://shop.example/products/magnetic-posture-corrector"


def page_rec(pid, content, link=None, likes=10, comments=2, shares=1, views=None, page="pg77"):
    return {"post_id": pid, "url": f"https://www.facebook.com/lunastore/posts/{pid}", "content": content, "post_external_link": link, "likes": likes,
            "num_comments": comments, "num_shares": shares, "video_view_count": views, "profile_id": page, "date_posted": "2026-10-01T10:00:00.000Z",
            "attachments": [{"type": "Photo", "thumbnail_url": f"https://cdn.example/{pid}.jpg"}], "is_sponsored": False}


def meta_ad(db, text=COPY, landing=LAND, page="pg77"):
    a = mk_ad(db, source="meta_library", network="meta", landing=landing, text=text, snapshot="https://www.facebook.com/ads/library/?id=1")
    a.page_id = page
    a.platform = "facebook"
    db.flush()
    return a


def fake_pages(monkeypatch, recs, calls):
    def handler(req: httpx.Request):
        calls.append(req)
        assert req.url.params["dataset_id"] == "gd_lkaxegm826bjpoo9m5"
        body = req.read().decode()
        assert '"num_of_posts":20' in body.replace(" ", "")
        return httpx.Response(200, json=recs)

    fake_bd(monkeypatch, handler)


def test_page_posts_are_not_fetched_without_a_click(db, monkeypatch):
    monkeypatch.setattr(bd, "submit", lambda *a, **k: pytest.fail("no request without an explicit lookup"))
    a = meta_ad(db)
    r = service.resolve(db, a.id, allow_provider=False)
    assert (r["status"], r["reason"]) == ("needs_provider", "page_posts_not_fetched")
    assert service.summary(db, [a.id])[a.id]["post"] is None


def test_landing_plus_copy_attaches_the_post_and_one_request_serves_the_whole_page(db, monkeypatch):
    calls = []
    fake_pages(monkeypatch, [page_rec("111", COPY + " Order today!", LAND, likes=321, comments=12, shares=4, views=9000),
                             page_rec("222", "Totally different post about a summer sale", "https://shop.example/products/other")], calls)
    a = meta_ad(db)
    b = meta_ad(db, text="Another creative: stand tall, a magnetic back corrector for everyday wear", landing=LAND + "?utm=x")
    r = service.resolve(db, a.id, allow_provider=True)
    db.commit()
    assert r["status"] == "ok" and len(calls) == 1
    s = service.summary(db, [a.id, b.id])
    assert (s[a.id]["post"]["likes"], s[a.id]["post"]["comments"], s[a.id]["post"]["shares"], s[a.id]["post"]["views"]) == (321, 12, 4, 9000)
    assert s[a.id]["post"]["post_id"] == "111" and s[a.id]["post"]["url"].endswith("/posts/111")
    hist = db.scalar(select(AdEngagement).where(AdEngagement.ad_id == a.id, AdEngagement.scope == "post", AdEngagement.status == "ok"))
    assert set(hist.evidence["matched_by"]) == {"landing", "copy"} and hist.evidence["score"] >= matching.ACCEPT
    assert s[b.id]["post"] is None and s[b.id]["reason"] == "no_confident_post_match"   # same landing page alone (.85) is not enough: no second family
    assert service.resolve(db, b.id, allow_provider=True)["status"] == "unmapped" and len(calls) == 1   # the page is cached: judged again, no second request
    assert db.scalar(select(func.count(PagePost.id)).where(PagePost.page_key == "pg77")) == 2


def test_no_confident_match_is_na_with_the_reason_never_the_nearest_post(db, monkeypatch):
    fake_pages(monkeypatch, [page_rec("333", "Summer sale on swimwear, everything must go", "https://shop.example/products/swimwear")], [])
    a = meta_ad(db)
    assert service.resolve(db, a.id, allow_provider=True)["status"] == "unmapped"
    s = service.summary(db, [a.id])[a.id]
    assert s["post"] is None and s["reason"] == "no_confident_post_match"


def test_two_equally_good_posts_are_ambiguous_not_a_coin_flip(db, monkeypatch):
    fake_pages(monkeypatch, [page_rec("441", COPY, LAND), page_rec("442", COPY, LAND)], [])
    a = meta_ad(db)
    service.resolve(db, a.id, allow_provider=True)
    s = service.summary(db, [a.id])[a.id]
    assert s["post"] is None and s["reason"] == "ambiguous_post_match"


def test_copy_alone_needs_the_picture_to_agree(db, monkeypatch):
    fake_pages(monkeypatch, [page_rec("551", COPY)], [])
    monkeypatch.setattr(matching, "ad_phash", lambda db, ad_id: "a5a5a5a5a5a5a5a5")
    monkeypatch.setattr(matching, "post_phash", lambda db, post: "0123456789abcdef")           # a different picture
    a = meta_ad(db, landing=None)
    service.resolve(db, a.id, allow_provider=True)
    assert service.summary(db, [a.id])[a.id]["post"] is None
    monkeypatch.setattr(matching, "post_phash", lambda db, post: "a5a5a5a5a5a5a5a4")           # the same picture, one bit off
    b = meta_ad(db, landing=None, page="pg77")
    db.commit()
    matching.best_post  # (the page is cached: re-match from the stored posts, no request)
    service.apply_matches(db, "pg77")
    assert service.summary(db, [b.id])[b.id]["post"]["post_id"] == "551"
    hist = db.scalar(select(AdEngagement).where(AdEngagement.ad_id == b.id, AdEngagement.status == "ok", AdEngagement.scope == "post"))
    assert set(hist.evidence["matched_by"]) == {"copy", "phash"}


def test_slow_page_collection_is_kept_as_a_snapshot_and_collected_free(db, monkeypatch):
    monkeypatch.setattr(bd.time, "sleep", lambda s: None)
    state = {"ready": False}

    def handler(req: httpx.Request):
        p = req.url.path
        if p.endswith("/scrape"):
            return httpx.Response(202, json={"snapshot_id": "sd_page1"})
        if "/progress/" in p:
            return httpx.Response(200, json={"status": "ready" if state["ready"] else "running"})
        return httpx.Response(200, json=[page_rec("661", COPY, LAND, likes=77)])

    fake_bd(monkeypatch, handler)
    a = meta_ad(db)
    r = service.resolve(db, a.id, allow_provider=True)
    db.commit()
    assert (r["status"], r["reason"]) == ("pending", "page_posts_pending")
    assert db.scalar(select(PageFetch.status).where(PageFetch.page_key == "pg77")) == "pending"
    assert quota_used(db, "brightdata_posts") == 1                    # accepted by Bright Data: billed once
    state["ready"] = True
    assert service.collect_pending(db) == {"collected": 1}            # free: progress + snapshot polling only
    db.commit()
    assert service.summary(db, [a.id])[a.id]["post"]["likes"] == 77 and quota_used(db, "brightdata_posts") == 1


def test_watching_a_competitor_shows_in_the_watched_list_at_once(db):
    """Audit 2026-10-14: the memoised competitor list ignored a new watch for up to 60 s."""
    from app.models import Advertiser

    adv = Advertiser(name=f"Audit Shop {RUN}")
    db.add(adv)
    db.flush()
    a = mk_ad(db, source="meta_library", network="meta")
    a.advertiser_id, a.country = adv.id, "PH"
    db.commit()
    c = TestClient(app, headers={"X-Admin-Token": "adm"})
    ids = lambda: {r["id"] for r in c.get("/api/competitor-radar", params={"watched_only": "true", "limit": 500}).json()["rows"]}
    assert adv.id not in ids()
    assert c.post(f"/api/competitor-radar/{adv.id}/watch", params={"watched": "true"}).status_code == 200
    assert adv.id in ids()
