"""Account policy 2026-10-19: one account type (MKT, active on registration, no ADMIN) and soft deduplication —
other MKTs' Save / Favorite lower the ranking and show "Sản phẩm đã có người quan tâm" without hiding or locking
anything; real exclusivity stays with Nhận / Bắt đầu test. Disposable DB only."""
from datetime import datetime

import pytest
from sqlalchemy import func, select, text

from app import allocation as A, interest as I
from app.db import SessionLocal, engine
from app.models import Ad
from app.workspace.models import User, UserActivity, UserSession
from test_allocation import fresh, users  # noqa: F401 — fresh: autouse disposable DB per test
from test_personal_search import item, reset  # noqa: F401 — reset: caches + no AI
from test_workspace import PASSWORD, client, write


@pytest.fixture(autouse=True)
def default_weights(monkeypatch):
    for k in ("RECO_INTEREST_PENALTY_SAVED", "RECO_INTEREST_PENALTY_FAVORITE", "RECO_INTEREST_PENALTY_PER_EXTRA",
              "RECO_INTEREST_PENALTY_MAX", "RECO_EXPLORE_SHARE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("RECO_EXPLORE_SHARE", "0")  # rank purely by score in these tests
    I._SET.update(at=0.0, v={})


def explain(c, pid):
    r = c.get(f"/api/recommendations/explain/{pid}")
    assert r.status_code == 200, r.text
    return r.json()


def search_card(c, pid, **q):
    rows = c.get("/api/search", params={"limit": 100, **q}).json()["rows"]
    return next((r for r in rows if r["id"] == pid), None)


def test_two_mkts_can_save_and_favorite_the_same_product_and_nothing_is_hidden():
    users()
    pid, ad = item("Polarized sunglasses")
    ca, cb = client("mkt-a@example.test"), client("mkt-b@example.test")
    for c in (ca, cb):
        assert write(c, "PUT", f"/workspace/ads/{ad}", {"saved": True, "favorite": True}).status_code == 200
        assert write(c, "POST", f"/library/products/{pid}/save", {"on": True}).status_code == 200
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(A.ProductAllocation)) == 0  # interest never locks
    for c in (ca, cb):
        assert search_card(c, pid, q="sunglasses") is not None                      # plain search: visible
        assert search_card(c, pid, q="sunglasses", mode="claimable") is not None   # still claimable
        assert c.get("/api/library", params={"section": "favorite"}).json()["total"] == 1
    assert write(cb, "POST", "/allocations/claim", {"product_id": pid}).status_code == 200  # first explicit Nhận wins
    assert write(ca, "POST", "/allocations/claim", {"product_id": pid}).status_code == 409


def test_label_shows_interest_but_never_who_and_not_for_own_interest():
    a, b, c = users()
    pid, ad = item("Polarized sunglasses")
    ca, cb = client("mkt-a@example.test"), client("mkt-b@example.test")
    write(ca, "PUT", f"/workspace/ads/{ad}", {"saved": True, "note": "A private"})
    card_b, card_a = search_card(cb, pid, q="sunglasses"), search_card(ca, pid, q="sunglasses")
    assert card_b["interest"] == {"label": I.LABEL, "level": "saved"} and card_a["interest"] is None
    write(ca, "PUT", f"/workspace/ads/{ad}", {"favorite": True})
    assert search_card(cb, pid, q="sunglasses")["interest"]["level"] == "favorite"
    for payload in (search_card(cb, pid, q="sunglasses"), explain(cb, pid),
                    cb.get("/api/recommendations?limit=50").json(), cb.get("/api/library", params={"section": "similar"}).json()):
        dump = str(payload)
        assert "mkt-a" not in dump and "A private" not in dump and f"'user_id': {a}" not in dump
    assert explain(cb, pid)["interest"]["label"] == I.LABEL and explain(ca, pid)["interest"] is None


def test_recommendation_prefers_products_nobody_else_wants_and_favorite_weighs_more():
    users()
    x, ad_x = item("Sunglasses classic")
    y, ad_y = item("Sunglasses classic")      # same evidence as x
    z, ad_z = item("Sunglasses classic")
    ca, cb = client("mkt-a@example.test"), client("mkt-b@example.test")
    base = explain(cb, x)["score"]
    assert explain(cb, y)["score"] == base == explain(cb, z)["score"]
    write(ca, "PUT", f"/workspace/ads/{ad_x}", {"saved": True})
    write(ca, "PUT", f"/workspace/ads/{ad_y}", {"favorite": True})
    sx, sy, sz = explain(cb, x), explain(cb, y), explain(cb, z)
    assert sz["score"] == base and sx["score"] < base and sy["score"] < sx["score"]  # favorite penalised more than saved
    assert sx["score_before_interest"] == base and sx["interest_penalty"] == pytest.approx(0.15)
    assert sy["interest_penalty"] == pytest.approx(0.30)
    assert any("MKT khác quan tâm" in r for r in sy["reasons"])
    order = [r["product_id"] for r in cb.get("/api/recommendations?limit=50").json()["rows"]]
    assert order.index(z) < order.index(x) < order.index(y)
    own = explain(ca, x)  # A's own Save / Favorite never penalise A (A's profile changed, so the raw score may move)
    assert "interest_penalty" not in own and own["interest"] is None


def test_penalty_weights_are_configurable(monkeypatch):
    users()
    x, ad_x = item("Sunglasses classic")
    ca, cb = client("mkt-a@example.test"), client("mkt-b@example.test")
    write(ca, "PUT", f"/workspace/ads/{ad_x}", {"saved": True})
    monkeypatch.setenv("RECO_INTEREST_PENALTY_SAVED", "0.5")
    assert explain(cb, x)["interest_penalty"] == pytest.approx(0.5)
    monkeypatch.setenv("RECO_INTEREST_PENALTY_SAVED", "0")
    e = explain(cb, x)
    assert "interest_penalty" not in e and e["interest"]["label"] == I.LABEL  # label stays, ranking unchanged


def test_unsave_and_unfavorite_update_the_signal_immediately():
    users()
    x, ad_x = item("Sunglasses classic")
    ca, cb = client("mkt-a@example.test"), client("mkt-b@example.test")
    base = explain(cb, x)["score"]
    fit_before = search_card(cb, x, q="sunglasses", mode="claimable", sort="fit")["fit"]["score"]
    write(ca, "PUT", f"/workspace/ads/{ad_x}", {"favorite": True})
    assert explain(cb, x)["score"] < base
    assert search_card(cb, x, q="sunglasses", mode="claimable", sort="fit")["fit"]["score"] < fit_before  # search cache noticed
    write(ca, "PUT", f"/workspace/ads/{ad_x}", {"favorite": False})
    assert explain(cb, x)["score"] == base and explain(cb, x)["interest"] is None
    assert search_card(cb, x, q="sunglasses", mode="claimable", sort="fit")["fit"]["score"] == fit_before
    write(ca, "POST", f"/library/products/{x}/save", {"on": True})
    assert explain(cb, x)["interest"]["level"] == "saved"
    write(ca, "POST", f"/library/products/{x}/save", {"on": False})
    assert explain(cb, x)["interest"] is None


def test_many_duplicate_ads_of_one_product_count_as_one_interest():
    users()
    x, ad1 = item("Sunglasses classic")
    y, _ = item("Sunglasses classic")
    with SessionLocal.begin() as db:
        extra = [Ad(external_id=f"dup-{i}", source="t", platform="facebook", product_id=x, title="dup", is_active=True) for i in range(3)]
        db.add_all(extra); db.flush(); dups = [a.id for a in extra]
    ca, cb, cc = client("mkt-a@example.test"), client("mkt-b@example.test"), client("mkt-c@example.test")
    for ad in [ad1, *dups]:
        write(ca, "PUT", f"/workspace/ads/{ad}", {"saved": True})
    one = explain(cc, x)["interest_penalty"]
    assert one == pytest.approx(0.15)  # four ads, one person → one saved signal
    write(cb, "PUT", f"/workspace/ads/{ad1}", {"saved": True})
    assert explain(cc, x)["interest_penalty"] == pytest.approx(0.20)  # a second person adds a little
    assert explain(cc, y)["interest"] is None


def test_interest_and_ownership_are_reported_separately():
    users()
    pid, ad = item("Sunglasses classic")
    ca, cb, cc = client("mkt-a@example.test"), client("mkt-b@example.test"), client("mkt-c@example.test")
    write(ca, "PUT", f"/workspace/ads/{ad}", {"saved": True})
    aid = write(cb, "POST", "/library/products/%d/start-test" % pid).json()["allocation_id"]
    card = search_card(cc, pid, q="sunglasses")
    assert card["interest"]["level"] == "saved" and card["allocation"] == "taken" and card["taken_state"] == "testing"
    assert search_card(cc, pid, q="sunglasses", mode="claimable") is None  # owned → gone from the exclusive view only
    write(cc, "POST", f"/library/products/{pid}/save", {"on": True})
    row = cc.get("/api/library").json()["rows"][0]
    assert row["interest"]["level"] == "saved" and row["ownership"] == "taken" and row["taken_state"] == "testing"
    assert row["library"]["allocation"] is None and row["library"]["result"] is None
    r = write(cc, "POST", f"/library/products/{pid}/start-test")
    assert r.status_code == 409 and r.json()["detail"]["state"] == "testing"
    write(cb, "POST", f"/allocations/{aid}/release", {})
    assert search_card(cc, pid, q="sunglasses")["allocation"] == "available"


def test_old_admin_api_is_closed_for_everyone():
    users()
    ca = client("mkt-a@example.test")
    for method, path, body in (("GET", "/api/admin/users", None), ("PATCH", "/api/admin/users/1", {"status": "active"}),
                               ("GET", "/api/admin/reset-requests", None), ("POST", "/api/admin/allocations/reassign", {"product_id": 1, "user_id": 1}),
                               ("POST", "/api/collector/connectors", {}), ("POST", "/api/storage/cleanup", {}), ("GET", "/api/collector/connectors", None)):
        r = ca.request(method, path, json=body, headers={"X-CSRF-Token": ca.cookies.get("toolspy_csrf", "")})
        assert r.status_code == 403, (path, r.status_code)
    from fastapi.testclient import TestClient
    from app.main import app
    import os
    os.environ["ADMIN_TOKEN"] = "svc-test"
    try:
        svc = TestClient(app, headers={"X-Admin-Token": "svc-test"})
        assert svc.get("/api/admin/users").status_code in (401, 404)  # the internal token never unlocks account data
        assert svc.get("/api/library").status_code == 401
    finally:
        os.environ.pop("ADMIN_TOKEN", None)
    me = ca.get("/api/auth/me").json()
    assert me["user"]["role"] == "MKT"


def test_register_and_use_immediately_without_approval():
    anon = client()
    r = write(anon, "POST", "/auth/register", {"name": "Newbie", "email": "newbie@example.test", "password": PASSWORD})
    assert r.status_code == 201
    c = client("newbie@example.test")
    assert c.get("/api/auth/me").json()["user"]["status"] == "active"
    assert c.get("/api/library").status_code == 200 and c.get("/api/recommendations").status_code == 200


def test_single_role_migration_keeps_everything_and_is_idempotent():
    from app.workspace.migrate import single_role
    a, b, c = users()
    with SessionLocal.begin() as db:
        if engine.dialect.name == "postgresql":  # simulate a database from before the policy
            db.execute(text("ALTER TABLE users DROP CONSTRAINT IF EXISTS users_role_check"))
            db.execute(text("UPDATE users SET role = 'ADMIN' WHERE id = :i"), {"i": c})
        db.execute(text("UPDATE users SET status = 'pending' WHERE id = :i"), {"i": b})
        db.add(UserActivity(user_id=c, kind="login"))
    write(client("mkt-a@example.test"), "PUT", "/preferences", {"priority_markets": ["SA"]})
    with SessionLocal() as db:
        before = (db.scalar(select(func.count()).select_from(User)), db.scalar(select(func.count()).select_from(UserSession)),
                  db.scalar(select(func.count()).select_from(UserActivity)))
    for _ in range(2):
        with SessionLocal.begin() as db:
            single_role(db)
    with SessionLocal() as db:
        after = (db.scalar(select(func.count()).select_from(User)), db.scalar(select(func.count()).select_from(UserSession)),
                 db.scalar(select(func.count()).select_from(UserActivity)))
        assert after == before  # no user, session or history row removed
        assert set(db.scalars(select(User.role))) == {"MKT"} and set(db.scalars(select(User.status))) == {"active"}
    assert client("mkt-a@example.test").get("/api/preferences").json()["manual"] == {"priority_markets": ["SA"]}
    if engine.dialect.name == "postgresql":
        with SessionLocal() as db, pytest.raises(Exception):
            db.execute(text("UPDATE users SET role = 'ADMIN' WHERE id = :i"), {"i": a}); db.flush()


def test_penalty_can_be_changed_in_the_database_without_restart():
    from app.interest import AppSetting
    users()
    x, ad_x = item("Sunglasses classic")
    ca, cb = client("mkt-a@example.test"), client("mkt-b@example.test")
    write(ca, "PUT", f"/workspace/ads/{ad_x}", {"saved": True})
    assert explain(cb, x)["interest_penalty"] == pytest.approx(0.15)
    with SessionLocal.begin() as db:  # what the user runs: UPDATE / INSERT on app_settings
        db.add(AppSetting(key="RECO_INTEREST_PENALTY_SAVED", value="0.4"))
    I._SET.update(at=0.0)  # skip the 30 s cache window
    assert explain(cb, x)["interest_penalty"] == pytest.approx(0.4)
