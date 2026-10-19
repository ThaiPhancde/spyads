"""Phase 4 MKT preference profile: learned from behavior + Follows with weights and time decay, manual edits win,
default profile for new accounts, read / update / reset API, per-MKT isolation, no AI calls."""
from datetime import datetime, timedelta
import uuid

import pytest
from sqlalchemy import func, select

from app import preferences as P
from app.behavior import BehaviorEvent
from app.db import SessionLocal
from app.models import Ad, Advertiser, Product
from test_allocation import fresh, users  # noqa: F401 — fresh: autouse disposable DB per test
from test_workspace import client, write


@pytest.fixture(autouse=True)
def no_ai(monkeypatch):
    from app.services import llm

    def forbidden(*a, **k):
        raise AssertionError("preference profile must not call AI")
    monkeypatch.setattr(llm, "complete_json", forbidden)
    for k in ("PREFERENCE_HALF_LIFE_DAYS", "PREFERENCE_MAX_AGE_DAYS", "PREFERENCE_DEFAULT_MARKETS"):
        monkeypatch.delenv(k, raising=False)


def item(category, country="US", language="en", price=None, currency=None, advertiser=None):
    with SessionLocal.begin() as db:
        i = uuid.uuid4().hex[:10]
        p = Product(product_code=f"PF-{i}", canonical_name=f"{category} {i}", category=category, country=country,
                    price=price, currency=currency, name_status="AUTO_CONFIRMED")
        db.add(p); db.flush()
        a = Ad(external_id=f"pf-{i}", source="meta_library", platform="facebook", product_id=p.id, advertiser_id=advertiser,
               country=country, language=language, title=category, is_active=True, first_seen_at=datetime(2026, 10, 1))
        db.add(a); db.flush()
        return p.id, a.id


def event(uid, kind, ad_id, days_ago):
    with SessionLocal.begin() as db:
        ad = db.get(Ad, ad_id)
        db.add(BehaviorEvent(user_id=uid, kind=kind, ad_id=ad_id, product_id=ad.product_id, advertiser_id=ad.advertiser_id,
                             data={}, dedupe_key=uuid.uuid4().hex, created_at=datetime.utcnow() - timedelta(days=days_ago)))


def keys(profile, dim):
    return [x["key"] for x in profile["learned"][dim]]


def test_new_account_gets_default_profile_without_writing():
    users()
    ca = client("mkt-a@example.test")
    p = ca.get("/api/preferences").json()
    assert p["is_default"] is True and p["learned"]["events_used"] == 0
    assert p["effective"]["markets"] == ["OFW_PH", "PH", "SA", "US", "EU"]
    assert "OFW_PH" in p["markets_available"]
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(P.PreferenceProfile)) == 0  # GET never writes


def test_learns_categories_from_save_favorite_and_tests():
    a, _, _ = users()
    _, beauty = item("beauty")
    _, kitchen = item("kitchen")
    _, toys = item("toys")
    ca = client("mkt-a@example.test")
    write(ca, "PUT", f"/workspace/ads/{beauty}", {"saved": True, "favorite": True})
    write(ca, "PUT", f"/workspace/ads/{kitchen}", {"saved": True})
    write(ca, "POST", "/behavior/events", {"kind": "ad_not_interested", "ad_ids": [toys]})
    write(ca, "POST", "/behavior/events", {"kind": "ad_view", "ad_ids": [toys]})
    p = ca.get("/api/preferences").json()
    assert keys(p, "categories") == ["beauty", "kitchen"]  # toys: view +0.2, not interested −3 → dropped
    assert p["learned"]["categories"][0]["score"] == 1.0 and p["is_default"] is False
    assert p["effective"]["categories"] == ["beauty", "kitchen"]


def test_test_outcomes_are_analysed_separately():
    a, _, _ = users()
    pw, _ = item("beauty")
    pl, _ = item("gadgets")
    ca = client("mkt-a@example.test")
    for pid, outcome in ((pw, "winner"), (pl, "loser")):
        aid = write(ca, "POST", "/allocations/claim", {"product_id": pid}).json()["id"]
        write(ca, "PATCH", f"/allocations/{aid}/status", {"status": "testing"})
        write(ca, "POST", f"/allocations/{aid}/results", {"outcome": outcome})
    t = ca.get("/api/preferences").json()["learned"]["tests"]
    assert t["success"] == 1 and t["fail"] == 1
    assert t["by_category"]["beauty"]["success"] == 1 and t["by_category"]["gadgets"]["fail"] == 1
    cats = {x["key"]: x["weight"] for x in ca.get("/api/preferences").json()["learned"]["categories"]}
    assert cats["beauty"] > cats["gadgets"]  # start 2 + success 6 vs start 2 − fail 1


def test_follow_adds_advertiser_and_what_it_advertises():
    users()
    with SessionLocal.begin() as db:
        adv = Advertiser(name="Gulf Shop", source="meta_library", source_advertiser_id="55", platform="facebook", country="SA")
        db.add(adv); db.flush(); adv_id = adv.id
    item("home", country="SA", language="tl", advertiser=adv_id)
    ca = client("mkt-a@example.test")
    write(ca, "POST", f"/competitors/{adv_id}/follow", {"interval_hours": 24})
    p = ca.get("/api/preferences").json()
    assert p["learned"]["followed_advertisers"] == [adv_id] and p["effective"]["advertisers"] == [adv_id]
    assert p["learned"]["advertisers"][0]["name"] == "Gulf Shop"
    assert keys(p, "categories") == ["home"]
    assert {"ME", "SA", "OFW_PH"} <= set(keys(p, "markets")) and keys(p, "languages") == ["tl"]
    write(ca, "DELETE", f"/competitors/{adv_id}/follow")
    assert ca.get("/api/preferences").json()["learned"]["followed_advertisers"] == []


def test_markets_countries_languages_and_price_range():
    users()
    ads = [item("fashion", "PH", "tl", 499, "PHP")[1], item("fashion", "PH", "tl", 799, "PHP")[1],
           item("fashion", "PH", "tl", 999, "PHP")[1], item("fashion", "US", "en", 20, "USD")[1]]
    ca = client("mkt-a@example.test")
    for ad in ads:
        write(ca, "PUT", f"/workspace/ads/{ad}", {"saved": True})
    p = ca.get("/api/preferences").json()
    assert keys(p, "countries")[0] == "PH" and keys(p, "markets")[0] == "PH" and "OFW_PH" not in keys(p, "markets")
    assert keys(p, "languages")[0] == "tl"
    pr = p["learned"]["price_range"]
    assert pr["currency"] == "PHP" and pr["samples"] == 3 and 499 <= pr["min"] <= pr["median"] <= pr["max"] <= 999
    assert p["effective"]["markets"][0] == "PH"  # learned markets replace defaults


def test_old_interactions_decay_and_very_old_are_ignored(monkeypatch):
    a, _, _ = users()
    _, old = item("garden")
    _, new = item("pets")
    _, ancient = item("books")
    event(a, "ad_save", old, 90)          # 2 × 0.5^3 = 0.25
    event(a, "ad_detail_view", new, 0)    # 0.5
    event(a, "ad_favorite", ancient, 400)  # beyond 365 days
    ca = client("mkt-a@example.test")
    p = ca.get("/api/preferences").json()
    assert keys(p, "categories") == ["pets", "garden"] and p["learned"]["events_used"] == 2
    w = {x["key"]: x["weight"] for x in p["learned"]["categories"]}
    assert w["garden"] == pytest.approx(0.25, abs=0.01)
    monkeypatch.setenv("PREFERENCE_HALF_LIFE_DAYS", "365")  # configurable: slower decay flips the order
    assert keys(ca.get("/api/preferences").json(), "categories")[0] == "garden"


def test_manual_edits_win_validate_and_clear():
    users()
    _, ad = item("beauty", "US")
    ca = client("mkt-a@example.test")
    write(ca, "PUT", f"/workspace/ads/{ad}", {"saved": True})
    r = write(ca, "PUT", "/preferences", {"priority_markets": ["OFW_PH", "SA"], "categories": ["kitchen"],
                                          "excluded_categories": ["beauty"], "price_min": 10, "price_max": 40, "currency": "usd"})
    assert r.status_code == 200, r.text
    e = r.json()["effective"]
    assert e["markets"] == ["OFW_PH", "SA"] and e["categories"] == ["kitchen"] and e["excluded_categories"] == ["beauty"]
    assert e["price_range"] == {"currency": "USD", "min": 10, "max": 40, "manual": True}
    assert r.json()["learned"]["categories"][0]["key"] == "beauty"  # learned kept, only hidden from effective
    assert write(ca, "PUT", "/preferences", {"priority_markets": ["MARS"]}).status_code == 422
    assert write(ca, "PUT", "/preferences", {"countries": ["Philippines"]}).status_code == 422
    assert write(ca, "PUT", "/preferences", {"price_min": 50}).status_code == 422  # 50 > existing max 40
    r = write(ca, "PUT", "/preferences", {"priority_markets": None, "countries": ["ph", "SA"]}).json()
    assert "priority_markets" not in r["manual"] and r["manual"]["countries"] == ["PH", "SA"]
    assert r["effective"]["markets"] == ["US"] and r["manual"]["categories"] == ["kitchen"]


def test_reset_manual_learned_and_all():
    a, _, _ = users()
    _, ad = item("beauty")
    ca = client("mkt-a@example.test")
    write(ca, "PUT", f"/workspace/ads/{ad}", {"saved": True})
    write(ca, "PUT", "/preferences", {"priority_markets": ["EU"]})
    r = write(ca, "POST", "/preferences/reset", {"scope": "manual"}).json()
    assert r["manual"] == {} and keys(r, "categories") == ["beauty"]
    write(ca, "PUT", "/preferences", {"priority_markets": ["EU"]})
    r = write(ca, "POST", "/preferences/reset", {"scope": "learned"}).json()
    assert r["learned"]["events_used"] == 0 and r["manual"]["priority_markets"] == ["EU"] and r["learned_reset_at"]
    with SessionLocal() as db:  # Phase 3 history itself is untouched
        assert db.scalar(select(func.count()).select_from(BehaviorEvent).where(BehaviorEvent.user_id == a)) == 1
    _, ad2 = item("kitchen")
    write(ca, "PUT", f"/workspace/ads/{ad2}", {"saved": True})
    assert keys(ca.get("/api/preferences").json(), "categories") == ["kitchen"]  # learns again after reset
    r = write(ca, "POST", "/preferences/reset", {"scope": "all"}).json()
    assert r["is_default"] is True and r["effective"]["markets"] == ["OFW_PH", "PH", "SA", "US", "EU"]


def test_profiles_are_isolated_between_mkts():
    a, b, c = users()
    _, beauty = item("beauty")
    _, kitchen = item("kitchen")
    ca, cb, cx = client("mkt-a@example.test"), client("mkt-b@example.test"), client("mkt-c@example.test")
    write(ca, "PUT", f"/workspace/ads/{beauty}", {"saved": True})
    write(cb, "PUT", f"/workspace/ads/{kitchen}", {"favorite": True})
    write(ca, "PUT", "/preferences", {"priority_markets": ["SA"]})
    pa, pb = ca.get("/api/preferences").json(), cb.get("/api/preferences").json()
    assert keys(pa, "categories") == ["beauty"] and keys(pb, "categories") == ["kitchen"]
    assert pa["effective"]["markets"] == ["SA"] and pb["manual"] == {}
    assert cb.get(f"/api/preferences?user_id={a}").status_code == 404
    write(cb, "POST", "/preferences/reset", {"scope": "all"})
    assert ca.get("/api/preferences").json()["manual"] == {"priority_markets": ["SA"]}
    assert cx.get(f"/api/preferences?user_id={a}").status_code == 404  # no account reads another's profile


def test_worker_batch_persists_only_stale_profiles():
    a, b, _ = users()
    _, ad = item("beauty")
    ca = client("mkt-a@example.test")
    write(ca, "PUT", f"/workspace/ads/{ad}", {"saved": True})
    assert P.refresh_stale() == 3  # A (history), B and C (default profiles)
    assert P.refresh_stale() == 0
    with SessionLocal() as db:
        row = db.get(P.PreferenceProfile, a)
        assert row.learned["categories"][0]["key"] == "beauty" and row.last_event_id > 0
        assert db.get(P.PreferenceProfile, b).learned["events_used"] == 0
    write(ca, "POST", "/behavior/events", {"kind": "ad_view", "ad_ids": [ad]})
    assert P.refresh_stale() == 1
    with SessionLocal.begin() as db:
        db.get(P.PreferenceProfile, b).computed_at = datetime.utcnow() - timedelta(hours=25)  # decay refresh
    assert P.refresh_stale() == 1
    r = write(ca, "POST", "/preferences/refresh").json()
    assert r["learned"]["events_used"] == 2


def test_auth_and_csrf():
    users()
    anon = client()
    assert anon.get("/api/preferences").status_code == 401
    assert write(anon, "PUT", "/preferences", {"priority_markets": ["US"]}).status_code == 401
    ca = client("mkt-a@example.test")
    assert ca.put("/api/preferences", json={"priority_markets": ["US"]}).status_code == 403
    assert ca.post("/api/preferences/reset", json={}).status_code == 403
