"""Video identity (design §2.2): near-identical cover frames on two pages share one video_key; facts count both pages and
the earliest sighting, across products; a far frame is another video; the API and product detail expose it."""
from datetime import datetime, timedelta

from app import videos
from app.db import SessionLocal
from app.models import Ad, Advertiser, Creative, Product
from app.visual.models import ProductImage
from test_allocation import fresh, users  # noqa: F401 — fresh: autouse disposable DB per test
from test_workspace import client


def ad_with_cover(db, page, phash, days_ago, active=True):
    adv = Advertiser(name=f"Page {page}", source="meta_library", source_advertiser_id=page, platform="facebook")
    p = Product(product_code=f"V-{page}", canonical_name=f"item {page}", name_status="AUTO_CONFIRMED", in_target=True)
    db.add_all([adv, p]); db.flush()
    a = Ad(external_id=f"v-{page}", source="meta_library", platform="facebook", network="meta", channel="ads", product_id=p.id,
           advertiser_id=adv.id, page_id=page, in_target=True, is_active=active, first_seen_at=datetime.utcnow() - timedelta(days=days_ago),
           last_seen_at=datetime.utcnow(), last_verified_at=datetime.utcnow() if active else None)
    db.add(a); db.flush()
    c = Creative(ad_id=a.id, product_id=p.id, type="video", source_url="u", status="stored", position=0)
    db.add(c); db.flush()
    db.add(ProductImage(source="creative", source_id=c.id, phash=phash, processing_status="done", indexed_at=datetime.utcnow()))
    return p.id


def test_same_cover_on_two_pages_is_one_video():
    users()
    with SessionLocal.begin() as db:
        old = ad_with_cover(db, "111", "a5a5a5a5a5a5a5a5", 40, active=False)  # stopped ad: its date still counts
        new = ad_with_cover(db, "222", "a5a5a5a5a5a5a5a7", 2)                  # 1 bit apart, another page + product
        ad_with_cover(db, "333", "5a5a5a5a5a5a5a5a", 1)                        # far: a different video
        ad_with_cover(db, "444", "0000000000000000", 1)                        # blank frame: never keyed
    with SessionLocal() as db:
        assert videos.assign(db) == 3 and videos.assign(db) == 0  # incremental: nothing left to key
        keys = {k for (k,) in db.query(ProductImage.video_key).filter(ProductImage.video_key.is_not(None)).distinct()}
        assert keys == {"a5a5a5a5a5a5a5a5", "5a5a5a5a5a5a5a5a"}
        f = videos.facts_for(db, ["a5a5a5a5a5a5a5a5"])["a5a5a5a5a5a5a5a5"]
    assert f["pages"] == 2 and f["ads"] == 2 and f["pages_unknown_id"] == 0 and f["verified_live_pages"] == 1 and f["new_pages_7d"] == 1
    assert f["video_first_seen"] == f["entrants"][0]["first_seen"] and f["entrants"][0]["page"] == "111"
    assert (datetime.utcnow() - f["video_first_seen"]).days == 40 and sorted(f["products"]) == sorted([old, new])

    c = client("mkt-a@example.test")
    r = c.get("/api/videos/a5a5a5a5a5a5a5a5")
    assert r.status_code == 200 and len(r.json()["ad_list"]) == 2
    assert c.get("/api/videos/ffff000000000000").status_code == 404
    vf = c.get(f"/api/products/{new}").json()["video_facts"]
    assert vf["count"] == 1 and vf["primary"]["video_key"] == "a5a5a5a5a5a5a5a5" and vf["primary"]["pages"] == 2
    rows = c.get("/api/creatives?video_key=a5a5a5a5a5a5a5a5").json()["rows"]  # Vault "Cùng video" filter (replaces dHash family)
    assert len(rows) == 2 and {r["video_size"] for r in rows} == {2}
