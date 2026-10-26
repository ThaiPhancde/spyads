"""Win checklist (design §2.4) and the four channel tabs (§2.5): each item is pass / fail / unknown with its number, and a
channel tab lists only products whose stored features match its rule, each row carrying its checklist."""
from datetime import datetime, timedelta
from types import SimpleNamespace

from app.db import SessionLocal
from app.models import Ad, Product, Store
from app.services.checklist import win_checklist
from test_allocation import fresh, users  # noqa: F401 — fresh: autouse disposable DB per test
from test_workspace import client

NOW = datetime(2026, 10, 26)
LIVE = {"active_ads": 3, "advertiser_count": 3, "new_advertisers_7d": 1, "marketplaces": ["1688", "aliexpress"], "supplier_price_usd": 2.1}


def status(items):
    return {i["key"]: i["status"] for i in items}


def test_checklist_statuses_for_a_crafted_product():
    video = {"video_first_seen": NOW - timedelta(days=10), "pages": 3}
    old_page = SimpleNamespace(domain="shop.test", page_published_at=NOW - timedelta(days=40))  # 30 days before the video
    items = win_checklist(LIVE, video, None, video["video_first_seen"], [old_page], NOW)
    assert status(items) == {"video_new": "pass", "page_new": "fail", "many_pages": "pass", "supplier": "pass"}
    assert items[0]["value"] == "10 ngày" and items[1]["value"] == "cũ hơn video 30 ngày" and items[3]["value"] == "~$2.1"

    # no video identity, own video ad 10 days old: the video may be older elsewhere → unknown; 40 days → certainly old
    lone = {"active_ads": 1, "advertiser_count": 1, "new_advertisers_7d": 0}
    young = win_checklist(lone, None, NOW - timedelta(days=10), NOW - timedelta(days=10), [], NOW)
    assert status(young) == {"video_new": "unknown", "page_new": "unknown", "many_pages": "fail", "supplier": "fail"}
    assert status(win_checklist(lone, None, NOW - timedelta(days=40), None, [], NOW))["video_new"] == "fail"
    fresh_page = SimpleNamespace(domain="new.test", page_published_at=NOW - timedelta(days=12))
    assert status(win_checklist({}, video, None, video["video_first_seen"], [fresh_page], NOW)) == \
        {"video_new": "pass", "page_new": "pass", "many_pages": "unknown", "supplier": "fail"}


def test_channel_tab_lists_only_matching_products():
    users()
    feats = {
        "win": {**LIVE, "ad_networks": ["meta"], "longevity_days": 10, "force_top": 50, "data_days": 40},
        "one_page": {**LIVE, "ad_networks": ["meta"], "advertiser_count": 1, "longevity_days": 10, "data_days": 40},
        "dead": {**LIVE, "ad_networks": ["meta"], "active_ads": 0, "longevity_days": 10, "data_days": 40},
        "tiktok": {**LIVE, "ad_networks": ["tiktok"], "advertiser_count": 2, "data_days": 5},
        "cow": {**LIVE, "ad_networks": ["meta"], "advertiser_count": 1, "longevity_days": 45, "ads_running_30d": 2, "data_days": 60},
        "spike": {**LIVE, "ad_networks": ["meta"], "advertiser_count": 1, "longevity_days": 45, "ads_running_30d": 2, "data_days": 60},
    }
    ids = {}
    with SessionLocal.begin() as db:
        for name, f in feats.items():
            p = Product(product_code=f"CH-{name}", canonical_name=name, name_status="AUTO_CONFIRMED", in_target=True, features=f,
                        potential={"vector": {"opportunity": 50, "momentum": 80 if name == "spike" else 30}},
                        first_seen_at=datetime.utcnow(), last_seen_at=datetime.utcnow())
            db.add(p); db.flush()
            ids[p.id] = name
        win_id = next(i for i, n in ids.items() if n == "win")
        db.add(Store(domain="shop.test", product_id=win_id, page_published_at=datetime.utcnow() - timedelta(days=3)))

    c = client("mkt-a@example.test")
    names = lambda tab: sorted(ids[r["id"]] for r in c.get(f"/api/discovery/tab/{tab}").json()["rows"])
    assert names("video_win") == ["win"]
    assert names("potential_tiktok") == ["tiktok"]
    assert names("cash_cow") == ["cow"]
    assert names("video_scale") == []  # creative_count missing everywhere → no product qualifies
    r = c.get("/api/discovery/tab/video_win").json()
    assert r["title"] == "Video Win" and r["tabs"]["cash_cow"] == "Bò sữa"
    chk = {i["key"]: i for i in r["rows"][0]["win_checklist"]}
    assert chk["many_pages"]["status"] == "pass" and chk["supplier"]["status"] == "pass" and chk["video_new"]["status"] == "unknown"

    d = c.get(f"/api/products/{win_id}").json()
    assert [i["key"] for i in d["win_checklist"]] == ["video_new", "page_new", "many_pages", "supplier"]
    assert not {"win_score", "rare_winner_score"} & d["product"].keys()

    # batch path (list payloads): video identity first, own video ad as the fallback, same result as product detail
    from app import videos
    from app.services.checklist import checklists
    from test_videos import ad_with_cover
    with SessionLocal.begin() as db:
        keyed, lone = ad_with_cover(db, "901", "a5a5a5a5a5a5a5a5", 50, active=False), ad_with_cover(db, "902", "a5a5a5a5a5a5a5a7", 5)
        own = ad_with_cover(db, "903", "0000000000000000", 45)  # blank cover: never keyed → own video ad date
        db.query(Ad).filter(Ad.product_id == own).update({"media_type": "video"})
    with SessionLocal() as db:
        videos.assign(db)
        out = checklists(db, db.query(Product).filter(Product.id.in_([keyed, lone, own])).all())
        assert out[lone][0]["status"] == "fail" and out[lone][0]["value"] == "50 ngày"  # the stopped ad on page 901 dates it
        assert out[own][0]["status"] == "fail" and out[own][0]["value"] == "45 ngày"
    assert c.get(f"/api/products/{lone}").json()["win_checklist"][0] == out[lone][0]
