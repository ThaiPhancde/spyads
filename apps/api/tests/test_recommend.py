"""Phase 5 personalized recommendations: per-user 0–100 fit, Follow priority, other MKTs' products excluded, failed tests
negative / inconclusive never success, explanations, cold start + exploration, renormalised weights without invented
signals, batch cache, access control. Disposable DB only."""
from datetime import datetime, timedelta
import uuid

import numpy as np
import pytest
from sqlalchemy import select

from app import recommend as R
from app.db import SessionLocal
from app.models import Ad, Advertiser, Product
from app.services import cluster_membership as CM
from test_allocation import fresh, users  # noqa: F401 — fresh: autouse disposable DB per test
from test_clustering import picture, vec
from test_workspace import client, write


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    R._MEMO.clear()
    R._VEC.update(at=0.0, v={})
    for k in ("RECO_EXPLORE_SHARE", "RECO_MIN_COVERAGE", "RECO_TTL_HOURS", "PREFERENCE_DEFAULT_MARKETS"):
        monkeypatch.delenv(k, raising=False)
    from app.services import llm
    monkeypatch.setattr(llm, "complete_json", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no AI")))


def item(category, country="PH", language="en", ads=5, advertisers=2, days=20, age=10, price=None, currency=None,
         advertiser=None, name=None, features=True, sub=None):
    now = datetime.utcnow()
    with SessionLocal.begin() as db:
        i = uuid.uuid4().hex[:8]
        p = Product(product_code=f"RC-{i}", canonical_name=name or f"{category} item {i}", category=category, subcategory=sub,
                    country=country, language=language, price=price, currency=currency, name_status="AUTO_CONFIRMED",
                    in_target=True, first_seen_at=now - timedelta(days=age), last_seen_at=now - timedelta(days=1),
                    features={"unique_ads": ads, "advertiser_count": advertisers, "longevity_days": days, "markets": [country]}
                    if features else {})
        db.add(p); db.flush()
        a = Ad(external_id=f"rc-{i}", source="meta_library", platform="facebook", product_id=p.id, advertiser_id=advertiser,
               country=country, language=language, title=p.canonical_name, is_active=True, first_seen_at=now - timedelta(days=age))
        db.add(a); db.flush()
        CM.sync(db, [a.id])
        return p.id, a.id


def rows(c, **q):
    qs = "&".join(f"{k}={v}" for k, v in q.items())
    return c.get(f"/api/recommendations?limit=200&{qs}").json()


def ids(feed):
    return [r["product_id"] for r in feed["rows"]]


def test_scores_are_personal_and_bounded():
    users()
    _, seed_b = item("beauty"); _, seed_k = item("kitchen")
    beauty, _ = item("beauty"); kitchen, _ = item("kitchen")
    ca, cb = client("mkt-a@example.test"), client("mkt-b@example.test")
    write(ca, "PUT", f"/workspace/ads/{seed_b}", {"saved": True})
    write(cb, "PUT", f"/workspace/ads/{seed_k}", {"favorite": True})
    fa, fb = rows(ca, explore="false"), rows(cb, explore="false")
    assert ids(fa)[0] == beauty and ids(fb)[0] == kitchen
    s = lambda c, pid: c.get(f"/api/recommendations/explain/{pid}").json()["score"]
    assert s(ca, beauty) > s(cb, beauty) and s(cb, kitchen) > s(ca, kitchen)
    assert all(0 <= r["score"] <= 100 for r in fa["rows"] + fb["rows"])
    top = fa["rows"][0]
    assert any("beauty" in x for x in top["reasons"]) and top["product"]["category"] == "beauty"
    assert set(top["components"]) == set(R.WEIGHTS) and fa["weights"]["category"] == 0.25


def test_followed_advertiser_products_are_prioritised():
    users()
    with SessionLocal.begin() as db:
        adv = Advertiser(name="Rival", source="meta_library", source_advertiser_id="77", platform="facebook", country="PH")
        db.add(adv); db.flush(); adv_id = adv.id
    followed, _ = item("garden", advertiser=adv_id)
    other, _ = item("garden")
    ca = client("mkt-a@example.test")
    write(ca, "POST", f"/competitors/{adv_id}/follow", {"interval_hours": 24})
    f = rows(ca, explore="false")
    order = ids(f)
    assert order.index(followed) < order.index(other)
    assert any("Follow" in x for x in f["rows"][order.index(followed)]["reasons"])


def test_products_owned_by_other_mkt_are_excluded_live():
    a, b, _ = users()
    p1, _ = item("toys"); p2, _ = item("toys")
    ca, cb = client("mkt-a@example.test"), client("mkt-b@example.test")
    assert R.refresh_stale() == 3
    assert {p1, p2} <= set(ids(rows(ca)))
    write(cb, "POST", "/allocations/claim", {"product_id": p1})  # after the batch: the cache must still hide it
    feed = rows(ca)
    assert feed["cached"] is True and p1 not in ids(feed) and p2 in ids(feed)
    assert ca.get(f"/api/recommendations/explain/{p1}").json()["excluded"] == "owned_by_other_mkt"
    assert p1 not in ids(rows(cb))  # own claimed product is no longer an "opportunity"
    assert cb.get(f"/api/recommendations/explain/{p1}").json()["excluded"] == "already_yours"


def claim_result(c, pid, outcome):
    aid = write(c, "POST", "/allocations/claim", {"product_id": pid}).json()["id"]
    write(c, "PATCH", f"/allocations/{aid}/status", {"status": "testing"})
    assert write(c, "POST", f"/allocations/{aid}/results", {"outcome": outcome}).status_code == 200


def test_failed_test_is_negative_and_inconclusive_is_not_success():
    users()
    create = lambda: item("pets", sub="leash", price=10, currency="USD")
    (won, _), (fail, _), (unsure, _) = create(), create(), create()
    candidate, _ = create()
    ca, cb = client("mkt-a@example.test"), client("mkt-b@example.test")
    claim_result(ca, won, "winner")
    claim_result(cb, fail, "loser")
    with SessionLocal() as db:
        p = db.get(Product, candidate)
        ctx_w, ctx_f = R.context(db, 1), R.context(db, 2)
        sim_w = R._similarity(ctx_w, p, False)
        sim_f = R._similarity(ctx_f, p, False)
    assert sim_w[0] == pytest.approx(1.0) and sim_f[0] == 0.0
    assert any("thất bại" in r for r in sim_f[1])
    ex_w = ca.get(f"/api/recommendations/explain/{candidate}").json()
    ex_f = cb.get(f"/api/recommendations/explain/{candidate}").json()
    assert ex_w["score"] > ex_f["score"]
    assert cb.get(f"/api/recommendations/explain/{fail}").json()["excluded"] == "already_yours"
    # inconclusive: a third user, same evidence, must score below the winner
    cc = client("mkt-c@example.test")  # users() creates three MKT accounts
    claim_result(cc, unsure, "inconclusive")
    ex_u = cc.get(f"/api/recommendations/explain/{candidate}").json()
    assert ex_f["score"] < ex_u["score"] < ex_w["score"]
    assert ex_u["components"]["similarity"]["score"] < ex_w["components"]["similarity"]["score"]


def test_semantic_uses_stored_clip_vectors():
    users()
    seed, seed_ad = item("home", name="ceramic vase")
    near, near_ad = item("decor", name="flower pot")
    far, far_ad = item("decor", name="flower pot two")
    with SessionLocal.begin() as db:
        for ad_id, v in ((seed_ad, vec(5)), (near_ad, vec(5) + 0.01), (far_ad, vec(99))):
            picture(db, db.get(Ad, ad_id), v)
    ca = client("mkt-a@example.test")
    write(ca, "PUT", f"/workspace/ads/{seed_ad}", {"saved": True})
    n = ca.get(f"/api/recommendations/explain/{near}").json()["components"]["semantic"]["score"]
    f = ca.get(f"/api/recommendations/explain/{far}").json()["components"]["semantic"]["score"]
    assert n > 0.9 and f < 0.2


def test_cold_start_and_exploration(monkeypatch):
    users()
    for cat in ("beauty", "kitchen", "toys", "garden", "pets", "tools"):
        item(cat, country="PH")
    item("auto", country="DE", ads=1, advertisers=1, days=1)
    ca = client("mkt-a@example.test")
    f = rows(ca)
    assert f["cold_start"] is True and f["total"] == 7
    top = f["rows"][0]
    assert top["components"]["category"]["score"] is None and top["components"]["similarity"]["score"] is None
    assert top["components"]["category"]["effective_weight"] == 0 and top["components"]["category"]["missing"] == "mkt"
    assert top["components"]["market"]["effective_weight"] == pytest.approx(0.15 / 0.35, abs=0.001)
    assert top["coverage"] == 1.0 and any("Hồ sơ mới" in r for r in top["reasons"])
    assert not any(r["explore"] for r in f["rows"])  # nothing is "outside" an empty profile: no exploration label
    worst = min(f["rows"], key=lambda r: r["score"])
    assert worst["product"]["country"] == "DE"  # PH (default priority) and stronger real signals score higher
    # with a beauty interest, other categories still show up as labelled exploration
    _, seed = item("beauty")
    write(ca, "PUT", f"/workspace/ads/{seed}", {"saved": True})
    f = rows(ca, limit=10)
    explored = [r for r in f["rows"] if r["explore"]]
    assert f["cold_start"] is False and explored and all(r["product"]["category"] != "beauty" for r in explored)
    assert all(any("Khám phá" in x for x in r["reasons"]) for r in explored)
    R._MEMO.clear()
    monkeypatch.setenv("RECO_EXPLORE_SHARE", "0")
    assert not any(r["explore"] for r in rows(ca)["rows"])


def test_missing_data_renormalises_without_inventing_signals():
    users()
    full, _ = item("beauty")
    bare, _ = item("beauty", features=False)
    ca = client("mkt-a@example.test")
    write(ca, "PUT", "/preferences", {"categories": ["beauty"]})
    e_full = ca.get(f"/api/recommendations/explain/{full}").json()
    e_bare = ca.get(f"/api/recommendations/explain/{bare}").json()
    v = e_bare["components"]["verified"]
    assert v["score"] is None and v["missing"] == "product" and v["effective_weight"] > 0  # unknown earns nothing
    assert e_bare["components"]["similarity"]["missing"] == "mkt" and e_bare["components"]["similarity"]["effective_weight"] == 0
    assert sum(c["effective_weight"] for c in e_bare["components"].values()) == pytest.approx(1.0, abs=0.01)
    assert e_bare["coverage"] < e_full["coverage"] == 1.0 and e_bare["score"] < e_full["score"]
    with SessionLocal() as db:  # unit level: a thin product is scaled down by coverage, never padded
        ctx = R.context(db, 1)
        p = db.get(Product, bare)
        p.first_seen_at, p.country, p.features, p.category = None, None, {}, None
        score, cov, comps, _ = R.score_product(ctx, p, {})
        assert cov == 0 and score == 0 and all(c["score"] is None for c in comps.values())
        db.rollback()


def test_excluded_category_not_interested_and_history_are_not_recommended():
    users()
    _, saved_ad = item("beauty")
    toy, toy_ad = item("toys"); tool, _ = item("tools"); keep, _ = item("kitchen")
    saved = None
    with SessionLocal() as db:
        saved = db.get(Ad, saved_ad).product_id
    ca = client("mkt-a@example.test")
    write(ca, "PUT", f"/workspace/ads/{saved_ad}", {"saved": True})
    write(ca, "POST", "/behavior/events", {"kind": "ad_not_interested", "ad_ids": [toy_ad]})
    write(ca, "PUT", "/preferences", {"excluded_categories": ["tools"]})
    got = set(ids(rows(ca)))
    assert keep in got and not ({saved, toy, tool} & got)
    reasons = {p: ca.get(f"/api/recommendations/explain/{p}").json()["excluded"] for p in (saved, toy, tool)}
    assert reasons == {saved: "already_in_your_history", toy: "not_interested", tool: "excluded_category"}


def test_batch_cache_and_staleness(monkeypatch):
    users()
    _, seed = item("beauty"); item("beauty"); item("kitchen")
    ca = client("mkt-a@example.test")
    calls = {"n": 0}
    real = R.compute

    def counting(*a, **k):
        calls["n"] += 1
        return real(*a, **k)
    monkeypatch.setattr(R, "compute", counting)
    rows(ca); rows(ca)
    assert calls["n"] == 1  # cache miss memoised, not recomputed per request
    assert R.refresh_stale() == 3 and R.refresh_stale() == 0
    calls["n"] = 0
    f = rows(ca)
    assert f["cached"] is True and calls["n"] == 0  # fresh cache: no scoring in the request
    with SessionLocal() as db:
        assert db.scalar(select(R.RecommendationState.candidates).where(R.RecommendationState.user_id == 1)) == 3
    write(ca, "PUT", f"/workspace/ads/{seed}", {"saved": True})  # profile/seed change → stale
    assert rows(ca)["cached"] is False
    assert R.refresh_stale() == 1
    with SessionLocal.begin() as db:
        db.get(R.RecommendationState, 1).computed_at = datetime.utcnow() - timedelta(hours=7)
    assert R.refresh_stale() == 1  # TTL
    r = write(ca, "POST", "/recommendations/refresh")
    assert r.status_code == 200 and r.json()["candidates"] == 2  # saved product left the pool


def test_access_control():
    a, b, _ = users()
    item("beauty")
    ca, cb, cx = client("mkt-a@example.test"), client("mkt-b@example.test"), client("mkt-c@example.test")
    assert client().get("/api/recommendations").status_code == 401
    assert cb.get(f"/api/recommendations?user_id={a}").status_code == 404
    assert cb.get(f"/api/recommendations/explain/1?user_id={a}").status_code == 404
    assert cx.get(f"/api/recommendations?user_id={a}").status_code == 404  # no account reads another's feed
    assert ca.post("/api/recommendations/refresh").status_code == 403  # CSRF
    assert ca.get("/api/recommendations/explain/999999").status_code == 404


def test_exploration_share_counts_listed_rows_not_the_cache_size():
    users()
    _, seed = item("beauty")
    for cat in ("toys", "garden", "pets"):
        item(cat)
    ca = client("mkt-a@example.test")
    write(ca, "PUT", f"/workspace/ads/{seed}", {"saved": True})
    rows = ca.get("/api/recommendations?limit=200").json()["rows"]
    assert len(rows) == 3 and sum(r["explore"] for r in rows) == 1  # round(3 × 20 %) — not every row
