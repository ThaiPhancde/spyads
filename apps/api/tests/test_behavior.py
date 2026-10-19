"""Phase 3 MKT behavior tracking: every action tied to the session user, no duplicates on repeated clicks or races,
existing Save / Favorite / Test / Follow endpoints feed it, views never grant ownership, MKT histories isolated."""
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest
from sqlalchemy import func, select

from app import allocation as A
from app.behavior import BehaviorEvent
from app.db import SessionLocal
from app.models import Advertiser
from test_allocation import fresh, seed, users  # noqa: F401 — fresh: autouse disposable DB per test
from test_workspace import client, write


def kinds(uid):
    with SessionLocal() as db:
        return [k for k in db.scalars(select(BehaviorEvent.kind).where(BehaviorEvent.user_id == uid).order_by(BehaviorEvent.id))]


def signal(c, kind, *ad_ids):
    return write(c, "POST", "/behavior/events", {"kind": kind, "ad_ids": list(ad_ids)})


def test_views_and_detail_are_deduplicated_and_never_grant_ownership():
    a, _, _ = users()
    (pid, ad1), (_, ad2) = seed(2)
    ca = client("mkt-a@example.test")
    assert signal(ca, "ad_view", ad1, ad2, ad1).json()["recorded"] == 2
    assert signal(ca, "ad_view", ad1).json()["recorded"] == 0  # same window
    assert signal(ca, "ad_detail_view", ad1).json()["recorded"] == 1
    assert signal(ca, "ad_detail_view", ad1).json()["recorded"] == 0
    assert signal(ca, "ad_view", 999999).json()["recorded"] == 0  # unknown ad ignored
    assert sorted(kinds(a)) == ["ad_detail_view", "ad_view", "ad_view"]
    with SessionLocal() as db:
        e = db.scalar(select(BehaviorEvent).where(BehaviorEvent.kind == "ad_detail_view"))
        assert e.ad_id == ad1 and e.product_id == pid
        assert db.scalar(select(func.count()).select_from(A.ProductAllocation)) == 0
    assert ca.get(f"/api/allocations/status/{pid}").json()["status"] == "available"


def test_view_window_is_configurable(monkeypatch):
    a, _, _ = users()
    (_, ad1), = seed()
    ca = client("mkt-a@example.test")
    signal(ca, "ad_view", ad1)
    from app import behavior
    monkeypatch.setattr(behavior, "_window", lambda kind, now: "next-window")
    assert signal(ca, "ad_view", ad1).json()["recorded"] == 1
    assert kinds(a) == ["ad_view", "ad_view"]


def test_save_favorite_toggles_record_only_real_changes():
    a, _, _ = users()
    (_, ad1), = seed()
    ca = client("mkt-a@example.test")
    for _ in range(3):  # triple click on Save
        assert write(ca, "PUT", f"/workspace/ads/{ad1}", {"saved": True}).status_code == 200
    write(ca, "PUT", f"/workspace/ads/{ad1}", {"favorite": True})
    write(ca, "PUT", f"/workspace/ads/{ad1}", {"favorite": True, "note": "hay"})
    write(ca, "PUT", f"/workspace/ads/{ad1}", {"favorite": False})
    write(ca, "PUT", f"/workspace/ads/{ad1}", {"favorite": False})
    write(ca, "PUT", f"/workspace/ads/{ad1}", {"saved": False})
    write(ca, "PUT", f"/workspace/ads/{ad1}", {"note": "chỉ ghi chú"})
    assert kinds(a) == ["ad_save", "ad_favorite", "ad_unfavorite", "ad_unsave"]


def test_concurrent_identical_clicks_record_once():
    a, _, _ = users()
    (_, ad1), = seed()
    clients = [client("mkt-a@example.test") for _ in range(4)]
    barrier = threading.Barrier(len(clients))

    def click(c):
        barrier.wait()
        return write(c, "PUT", f"/workspace/ads/{ad1}", {"saved": True}).status_code, signal(c, "ad_skip", ad1).status_code

    with ThreadPoolExecutor(max_workers=len(clients)) as pool:
        assert set(pool.map(click, clients)) == {(200, 200)}
    assert sorted(kinds(a)) == ["ad_save", "ad_skip"]


def test_skip_and_not_interested():
    a, _, _ = users()
    (_, ad1), = seed()
    ca = client("mkt-a@example.test")
    assert signal(ca, "ad_skip", ad1).json()["recorded"] == 1
    assert signal(ca, "ad_skip", ad1).json()["recorded"] == 0
    assert signal(ca, "ad_not_interested", ad1).json()["recorded"] == 1
    assert signal(ca, "ad_not_interested", ad1).json()["recorded"] == 0
    assert signal(ca, "ad_save", ad1).status_code == 422  # server-observed kinds can't be forged by the client
    assert signal(ca, "test_success", ad1).status_code == 422


def test_test_start_and_outcomes_are_distinct():
    a, _, _ = users()
    (pid, ad1), (pid2, _), (pid3, _) = seed(3)
    ca = client("mkt-a@example.test")
    write(ca, "PUT", f"/workspace/ads/{ad1}", {"tested": True})  # ad-level start, no allocation
    write(ca, "PUT", f"/workspace/ads/{ad1}", {"tested": True})
    for p, outcome in ((pid, "winner"), (pid2, "loser"), (pid3, "inconclusive")):
        aid = write(ca, "POST", "/allocations/claim", {"product_id": p}).json()["id"]
        assert write(ca, "PATCH", f"/allocations/{aid}/status", {"status": "testing"}).status_code == 200
        assert write(ca, "POST", f"/allocations/{aid}/results", {"outcome": outcome, "market": "PH"}).status_code == 200
    assert kinds(a) == ["test_start", "test_start", "test_success", "test_start", "test_fail", "test_start", "test_inconclusive"]
    with SessionLocal() as db:
        win = db.scalar(select(BehaviorEvent).where(BehaviorEvent.kind == "test_success"))
        assert win.product_id == pid and win.allocation_id and win.data["market"] == "PH"


def test_tested_click_on_reserved_product_links_the_allocation():
    a, _, _ = users()
    (pid, ad1), = seed()
    ca = client("mkt-a@example.test")
    aid = write(ca, "POST", "/allocations/claim", {"product_id": pid}).json()["id"]
    write(ca, "PUT", f"/workspace/ads/{ad1}", {"tested": True})
    with SessionLocal() as db:
        e = db.scalar(select(BehaviorEvent).where(BehaviorEvent.kind == "test_start"))
        assert e.allocation_id == aid and e.ad_id == ad1 and e.product_id == pid


def test_follow_unfollow_reuse_existing_endpoints_without_duplicates():
    a, _, _ = users()
    with SessionLocal.begin() as db:
        adv = Advertiser(name="Rival", source="meta_library", source_advertiser_id="901", platform="facebook", country="PH")
        db.add(adv); db.flush(); adv_id = adv.id
    ca = client("mkt-a@example.test")
    for _ in range(2):
        assert write(ca, "POST", f"/competitors/{adv_id}/follow", {"interval_hours": 24}).status_code == 200
    write(ca, "POST", f"/competitors/{adv_id}/follow", {"interval_hours": 6})  # interval change is not a new follow
    for _ in range(2):
        assert write(ca, "DELETE", f"/competitors/{adv_id}/follow").status_code == 200
    assert kinds(a) == ["advertiser_follow", "advertiser_unfollow"]
    with SessionLocal() as db:
        assert set(db.scalars(select(BehaviorEvent.advertiser_id))) == {adv_id}


def test_history_is_isolated_per_mkt_and_nobody_reads_another_account():
    a, b, c = users()
    (pid, ad1), = seed()
    ca, cb, cx = client("mkt-a@example.test"), client("mkt-b@example.test"), client("mkt-c@example.test")
    signal(ca, "ad_view", ad1)
    write(ca, "PUT", f"/workspace/ads/{ad1}", {"saved": True})
    signal(cb, "ad_view", ad1)  # B's view in the same window is B's own event
    ha = ca.get("/api/behavior/events").json()
    assert ha["total"] == 2 and {r["kind"] for r in ha["rows"]} == {"ad_view", "ad_save"} and "user_id" not in ha["rows"][0]
    assert ca.get("/api/behavior/events?user_id=%d" % b).json()["total"] == 2  # MKT can't widen scope
    assert cb.get("/api/behavior/events").json()["total"] == 1
    assert ca.get("/api/behavior/events?kind=ad_save").json()["rows"][0]["ad_id"] == ad1
    assert ca.get(f"/api/behavior/events?product_id={pid}").json()["total"] == 2
    assert ca.get("/api/behavior/summary").json()["counts"]["ad_save"] == 1
    assert cx.get("/api/behavior/events").json()["total"] == 0  # a third MKT sees only its own (empty) history
    assert cx.get(f"/api/behavior/events?user_id={b}").json()["total"] == 0
    assert cx.get(f"/api/behavior/summary?user_id={a}").json()["counts"]["ad_view"] == 0
    assert all("user_id" not in r for r in ca.get("/api/behavior/events").json()["rows"])


def test_requires_authenticated_user_and_csrf():
    users()
    (_, ad1), = seed()
    anon = client()
    assert anon.get("/api/behavior/events").status_code == 401
    assert signal(anon, "ad_view", ad1).status_code == 401
    ca = client("mkt-a@example.test")
    assert ca.post("/api/behavior/events", json={"kind": "ad_view", "ad_ids": [ad1]}).status_code == 403
    assert signal(ca, "ad_view").status_code == 422  # empty batch
    assert write(ca, "POST", "/behavior/events", {"kind": "ad_view", "ad_ids": list(range(1, 102))}).status_code == 422
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(BehaviorEvent)) == 0


def test_ads_listing_does_not_write_behavior():
    a, _, _ = users()
    seed(3)
    ca = client("mkt-a@example.test")
    assert ca.get("/api/ads?limit=10").status_code == 200
    assert kinds(a) == []
