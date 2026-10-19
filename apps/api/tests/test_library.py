"""Phase 8 personalized Library: per-user sections, product-level save / favorite / start test / result / release kept in
step with ad interactions and ownership, filters, similar + followed sections, privacy, persistence across re-login,
and no copy of Ads data. Disposable DB only."""
import pytest
from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import Ad, Advertiser, Product
from app.workspace.models import AdInteraction
from test_allocation import fresh, users  # noqa: F401 — fresh: autouse disposable DB per test
from test_personal_search import item, reset  # noqa: F401 — reset: autouse cache reset + no AI
from test_workspace import client, write


def lib(c, **q):
    r = c.get("/api/library", params=q)
    assert r.status_code == 200, r.text
    return r.json()


def ids(res):
    return [r["id"] for r in res["rows"]]


def card(res, pid):
    return next(r for r in res["rows"] if r["id"] == pid)


def test_crud_and_state_sync_through_the_whole_lifecycle():
    users()
    pid, ad = item("Polarized sunglasses")
    ca = client("mkt-a@example.test")
    assert lib(ca)["total"] == 0
    assert write(ca, "POST", f"/library/products/{pid}/save", {"on": True}).status_code == 200
    r = lib(ca)
    assert ids(r) == [pid] and r["counts"]["saved"] == 1 and card(r, pid)["library"]["saved"] is True
    assert card(r, pid)["library"]["ads"][0]["id"] == ad  # the original ad, referenced — not a copy
    with SessionLocal() as db:  # same row the Ads page writes: one source of truth
        assert db.get(AdInteraction, (1, ad)).saved is True
    write(ca, "POST", f"/library/products/{pid}/favorite", {"on": True})
    assert ids(lib(ca, section="favorite")) == [pid]
    write(ca, "PUT", f"/workspace/ads/{ad}", {"favorite": False})  # changed elsewhere → Library follows
    assert lib(ca, section="favorite")["total"] == 0
    # start test: claim + testing in one action
    t = write(ca, "POST", f"/library/products/{pid}/start-test")
    assert t.status_code == 200 and t.json()["status"] == "testing"
    c = card(lib(ca, section="testing"), pid)
    assert c["ownership"] == "mine" and c["library"]["allocation"]["status"] == "testing" and c["library"]["test_status"] == "testing"
    aid = c["library"]["allocation"]["id"]
    assert write(ca, "POST", f"/library/products/{pid}/start-test").json()["allocation_id"] == aid  # idempotent
    # update result
    assert write(ca, "POST", f"/allocations/{aid}/results", {"outcome": "winner", "market": "PH", "orders": 12}).status_code == 200
    r = lib(ca, section="tested")
    assert ids(r) == [pid] and card(r, pid)["library"]["result"]["outcome"] == "winner" and lib(ca, section="testing")["total"] == 0
    # return to pool (release): ownership goes, history and the saved ad stay
    assert write(ca, "POST", f"/allocations/{aid}/release", {}).status_code == 200
    c = card(lib(ca), pid)
    assert c["ownership"] == "available" and c["library"]["allocation"] is None and c["library"]["result"]["outcome"] == "winner"
    write(ca, "POST", f"/library/products/{pid}/save", {"on": False})
    with SessionLocal() as db:
        assert db.get(AdInteraction, (1, ad)).saved is False
    assert lib(ca, section="saved")["total"] == 0 and ids(lib(ca, section="tested")) == [pid]


def test_saving_a_reserved_product_keeps_it_a_reservation_with_ttl():
    """Account policy 2026-10-19: Save is a soft interest signal, never ownership — the reservation keeps its TTL."""
    users()
    pid, _ = item("Dog leash", category="pets")
    ca = client("mkt-a@example.test")
    aid = write(ca, "POST", "/allocations/claim", {"product_id": pid}).json()["id"]
    write(ca, "POST", f"/library/products/{pid}/save", {"on": True})
    c = card(lib(ca), pid)["library"]["allocation"]
    assert c["id"] == aid and c["status"] == "reserved" and c["reserved_until"] is not None


def test_other_mkts_data_never_shows_and_their_products_cannot_be_tested():
    a, b, c = users()
    pid, ad = item("Sunglasses")
    ca, cb, cx = client("mkt-a@example.test"), client("mkt-b@example.test"), client("mkt-c@example.test")
    write(cb, "POST", f"/library/products/{pid}/start-test")
    write(cb, "PUT", f"/workspace/ads/{ad}", {"note": "B's secret note"})
    assert lib(ca)["total"] == 0 and lib(cx)["total"] == 0  # a third MKT reads nothing of A's or B's
    assert write(ca, "POST", f"/library/products/{pid}/start-test").status_code == 409
    write(ca, "POST", f"/library/products/{pid}/save", {"on": True})
    c = card(lib(ca), pid)
    assert c["ownership"] == "taken" and c["library"]["allocation"] is None and c["library"]["result"] is None
    assert "B's secret note" not in str(c) and all(x["note"] in ("", None) for x in c["library"]["ads"])
    assert ca.get("/api/library", params={"user_id": b}).json()["total"] == 1  # unknown param ignored: still A's own
    assert card(lib(cb, section="testing"), pid)["library"]["ads"][0]["note"] == "B's secret note"


def test_filters():
    users()
    with SessionLocal.begin() as db:
        adv = Advertiser(name="Rival", source="meta_library", source_advertiser_id="9", platform="facebook"); db.add(adv); db.flush(); adv_id = adv.id
    sun, _ = item("Sunglasses", category="accessories", country="PH")
    leash, _ = item("Dog leash", category="pets", country="US", advertiser=adv_id)
    with SessionLocal.begin() as db:
        db.get(Product, leash).subcategory = "leash"
    ca = client("mkt-a@example.test")
    for p in (sun, leash):
        write(ca, "POST", f"/library/products/{p}/save", {"on": True})
    aid = write(ca, "POST", f"/library/products/{leash}/start-test").json()["allocation_id"]
    write(ca, "POST", f"/allocations/{aid}/results", {"outcome": "loser"})
    assert ids(lib(ca, category="pets")) == [leash]
    assert ids(lib(ca, market="PH")) == [sun] and ids(lib(ca, market="US")) == [leash]
    assert ids(lib(ca, product_type="leash")) == [leash]
    assert ids(lib(ca, test_status="loser")) == [leash] and ids(lib(ca, test_status="untested")) == [sun]
    assert lib(ca, test_status="winner")["total"] == 0
    assert ids(lib(ca, advertiser_id=adv_id)) == [leash]
    scores = {r["id"]: r["fit"]["score"] for r in lib(ca)["rows"]}
    assert all(0 <= s <= 100 for s in scores.values()) and all(r["fit"]["reasons"] is not None for r in lib(ca)["rows"])
    hi = max(scores.values())
    assert set(ids(lib(ca, min_fit=hi))) == {p for p, s in scores.items() if s >= hi}
    assert ca.get("/api/library", params={"section": "nope"}).status_code == 422
    assert ca.get("/api/library", params={"test_status": "nope"}).status_code == 422


def test_similar_and_followed_sections():
    users()
    with SessionLocal.begin() as db:
        adv = Advertiser(name="Rival", source="meta_library", source_advertiser_id="5", platform="facebook"); db.add(adv); db.flush(); adv_id = adv.id
    seed, _ = item("Polarized sunglasses for men")
    vi, _ = item("Kính mát phân cực nam", language="vi")
    other, _ = item("Rival lamp", category="home", advertiser=adv_id)
    ca = client("mkt-a@example.test")
    write(ca, "POST", f"/library/products/{seed}/save", {"on": True})
    r = lib(ca, section="similar")
    assert vi in ids(r) and seed not in ids(r)
    c = card(r, vi)
    assert c["fit"]["similarity"] > 0 and c["fit"]["because_of"][0]["product_id"] == seed and c["ownership"] == "available"
    assert lib(ca, section="followed")["advertisers"] == []
    write(ca, "POST", f"/competitors/{adv_id}/follow", {"interval_hours": 24})
    r = lib(ca, section="followed")
    assert ids(r) == [other] and r["advertisers"][0]["name"] == "Rival" and r["advertisers"][0]["products"] == 1
    assert card(r, other)["fit"]["followed_advertiser"] == "Rival"
    # similar products for a Library card, straight from the Phase 6 API
    sim = ca.get(f"/api/similar/products/{seed}", params={"exclusive": "true", "limit": 6}).json()
    assert vi in [x["product_id"] for x in sim["similar"]]


def test_library_survives_logout_and_login():
    users()
    pid, ad = item("Sunglasses")
    ca = client("mkt-a@example.test")
    write(ca, "POST", f"/library/products/{pid}/favorite", {"on": True})
    write(ca, "PUT", f"/workspace/ads/{ad}", {"note": "check supplier"})
    aid = write(ca, "POST", f"/library/products/{pid}/start-test").json()["allocation_id"]
    before = lib(ca)
    assert write(ca, "POST", "/auth/logout").status_code == 200
    assert ca.get("/api/library").status_code == 401
    again = client("mkt-a@example.test")
    after = lib(again)
    assert after["rows"] == before["rows"] and after["counts"] == before["counts"]
    c = card(after, pid)
    assert c["library"]["favorite"] and c["library"]["ads"][0]["note"] == "check supplier" and c["library"]["allocation"]["id"] == aid


def test_no_copy_of_ads_and_auth():
    users()
    pid, _ = item("Sunglasses")
    with SessionLocal() as db:
        before = (db.scalar(select(func.count()).select_from(Ad)), db.scalar(select(func.count()).select_from(Product)))
    ca = client("mkt-a@example.test")
    write(ca, "POST", f"/library/products/{pid}/save", {"on": True})
    write(ca, "POST", f"/library/products/{pid}/favorite", {"on": True})
    write(ca, "POST", f"/library/products/{pid}/start-test")
    lib(ca); lib(ca, section="similar")
    with SessionLocal() as db:
        assert (db.scalar(select(func.count()).select_from(Ad)), db.scalar(select(func.count()).select_from(Product))) == before
        assert db.scalar(select(func.count()).select_from(AdInteraction)) == 1  # two flags, one row, one ad
    assert client().get("/api/library").status_code == 401
    assert ca.post(f"/api/library/products/{pid}/save", json={"on": True}).status_code == 403  # CSRF
    assert write(ca, "POST", "/library/products/999999/save", {"on": True}).status_code == 404
