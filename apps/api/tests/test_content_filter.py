"""Content relevance filter: short drama / movie / streaming / podcast ads never stored, product ads always kept,
old entertainment rows purged without touching shared data. Cases are real ads from the DB (2026-10-13 audit).
Throw-away SQLite (conftest); raw batches, media downloads and file storage are stubbed — nothing leaves the test."""
from datetime import datetime

import pytest
from sqlalchemy import select

from app import ingest, media, purge_entertainment, purge_removed
from app.collector.contract import AdRecord, MediaItem
from app.db import SessionLocal
from app.models import Ad, AdSource, Creative, Product
from app.services.content_filter import ContentExclusion, classify

BLOCK = [  # (advertiser, title/text, cta, landing)
    ("Corey Barbara Siebor", "She schemed. She lied. She stole the wrong life.", "WATCH_MORE", "https://w2a.reelshort.com/w2a/booksAdvPageV2/?book_id=1"),
    ("Colección dramática", "Montones de grandes dramas cortos, actualizados diariamente.", "INSTALL_MOBILE_APP",
     "https://play.google.com/store/apps/details?id=com.freereels.app&hl=en"),
    ("shorttv", "Continue Watch", None, None),
    ("wonderful movie", "Continue to watch the full series", None, None),
    ("FigureO POD", "Bentley CEO on Extreme Safety: crash-test a $300,000+ car", "LISTEN_NOW", "https://open.spotify.com/episode/377V18"),
    ("Melolo Freedrama-PH", "Watch new dramas for free. Episode 1 to finale.", "WATCH_MORE", "https://play.google.com/store/apps/details?id=com.worldance.drama"),
    ("shortmax", "Weiter ansehen", None, None),
    ("Daily Drama Bites", "Keep Watching for FREE", "WATCH_MORE", "https://shortical.onelink.me/o2gt?pid=x"),
    ("الحد الأقصى القصير", "شاهد الحلقة الأولى من المسلسل الآن", None, None),
    ("短剧推荐", "全集免费看 第1集", None, None),
]
KEEP = [  # look like entertainment, sell a product
    ("Tech", "Portable Pocket Cinema projector — watch movies like in a cinema, Netflix ready", "SHOP_NOW", "https://pocketcine.store/products/mini-projector"),
    ("KLIK", "The Film Camera In Digital Form", "SHOP_NOW", "https://klikcamera.com/products/klik-camera"),
    ("Azja Sklep", "Film kolagenowy + nano spray", "SHOP_NOW", "https://azjasklep.com/products/zestaw"),
    ("Đồng Hồ Tân Tân", "Vì sao Citizen Series 8 từng chỉ bán ở Nhật", "MESSAGE_PAGE", None),
    ("Saeskyn", "No more TSA liquid drama. One Korean multi-balm.", "SHOP_NOW", "https://saeskyn.com/products/balm-stick"),
    ("Shopee", "Setiap hari diskon 25% & GRATIS ONGKIR untuk semua product fashion", "INSTALL_MOBILE_APP", "http://itunes.apple.com/app/id959841443"),
    ("Daily Relief Lab", "Her story: the neck pain got worse every night… until this device. 50% off today", "SHOP_NOW",
     "https://savifyshop.store/products/therapro-neck-massager"),  # storytelling VSL
    ("GpsNautico.com", "Hasta un 35% de Descuento Black!", "WATCH_MORE", "http://gpsnautico.com/"),
    ("Movie Night Gear", "Máy chiếu mini xem phim tại nhà, giá chỉ 1.290.000đ, freeship", "MESSAGE_PAGE", None),
    ("Reborn Dolls PH", "Reborn baby doll, lifelike, ₱1,499 COD", "SHOP_NOW", "https://reborndolls.ph/products/a"),
    ("Trailer Parts AU", "Heavy duty trailer hitch lock", "SHOP_NOW", "https://trailerparts.com.au/products/lock"),
]


@pytest.mark.parametrize("adv,text,cta,url", BLOCK)
def test_entertainment_is_blocked(adv, text, cta, url):
    assert classify(adv, None, text, cta, None, url)[0] == "block"


@pytest.mark.parametrize("adv,text,cta,url", KEEP)
def test_product_ads_are_kept(adv, text, cta, url):
    assert classify(adv, None, text, cta, None, url)[0] == "keep"


def test_single_weak_hint_goes_to_review_not_delete():
    assert classify("Boss Films", "Create custom tattoo designs with Tattoo AI!", None, "INSTALL_MOBILE_APP")[0] == "review"
    assert classify("Dr. Thread Lift", None, "Keep watching to see the result", None, None)[0] == "review"


def _rec(i, adv, text, url=None, cta=None, page="p1", **kw):
    return AdRecord(source="meta_library", source_ad_id=f"t{i}", platform="facebook", country="PH", page_id=page, advertiser=adv,
                    ad_text=text, cta_type=cta, landing_page=url, active=True,
                    media=[MediaItem(type="video", url=f"https://cdn.example/v{i}.mp4")], **kw)


@pytest.fixture
def quiet(monkeypatch, tmp_path):
    monkeypatch.setattr(ingest, "write_raw_batch", lambda *a, **k: None)
    monkeypatch.setattr(media, "enqueue", lambda *a, **k: None)
    deleted = []

    class FakeStore:
        def delete(self, key):
            deleted.append(key)
            return 1

    monkeypatch.setattr(purge_removed, "store", lambda: FakeStore())
    monkeypatch.setattr(purge_entertainment, "DATA_DIR", tmp_path)
    return deleted


def test_ingest_drops_drama_and_never_reingests(quiet):
    drama = _rec(1, "ReelShort", "Married a CEO, reclaimed my legacy", "https://fb.reelshort.com/x", "WATCH_MORE", page="drama")
    product = _rec(2, "Daily Relief Lab", "Neck massager 50% off today", "https://savifyshop.store/products/neck-massager", "SHOP_NOW",
                   page="shop")
    with SessionLocal() as db:
        s = ingest.ingest_ads(db, [drama, product])
        assert s["excluded_entertainment"] == 1 and s["new_ads"] == 1
        assert db.scalar(select(Ad).where(Ad.external_id == "t1")) is None
        assert db.scalar(select(Ad).where(Ad.external_id == "t2")) is not None
        ex = db.scalar(select(ContentExclusion).where(ContentExclusion.source_ad_id == "t1"))
        assert ex.decision == "block" and ex.rules_version and ex.reasons
        # next sync: same id comes back with harmless-looking copy → still excluded (no re-classification)
        again = _rec(1, "ReelShort", "New update", page="drama")
        assert ingest.ingest_ads(db, [again])["excluded_entertainment"] == 1
        assert db.scalar(select(Ad).where(Ad.external_id == "t1")) is None


def test_purge_deletes_only_entertainment_and_keeps_shared(quiet):
    with SessionLocal() as db:
        ingest.ingest_ads(db, [_rec(10, "Shop A", "Mini projector 1,299 PHP COD", "https://a.shop/products/projector", "SHOP_NOW", page="a")])
        good = db.scalar(select(Ad).where(Ad.external_id == "t10"))
        # an old drama ad that got in before the filter, sharing the product cluster and one stored file with the good ad
        bad = Ad(external_id="t11", source="meta_library", platform="facebook", product_id=good.product_id, page_id="d",
                 ad_text="Continue to watch the full series", landing_url="https://fb.netshort.com/h5", cta_type="WATCH_MORE",
                 first_seen_at=datetime.utcnow(), last_seen_at=datetime.utcnow(), channel="ads", network="meta")
        db.add(bad)
        db.flush()
        db.add(AdSource(ad_id=bad.id, source="meta_library", source_ad_id="t11"))
        db.add(AdSource(ad_id=bad.id, source="apify_meta", source_ad_id="x11"))  # also reported by another provider
        db.add_all([Creative(ad_id=bad.id, type="video", source_url="u1", storage_key="shared.mp4", status="stored", size_bytes=10),
                    Creative(ad_id=bad.id, type="video", source_url="u2", storage_key="only-bad.mp4", status="stored", size_bytes=20),
                    Creative(ad_id=good.id, type="video", source_url="u3", storage_key="shared.mp4", status="stored")])
        lonely = Ad(external_id="t12", source="meta_library", platform="facebook", ad_text="Free short dramas, updated daily",
                    landing_url="https://play.google.com/store/apps/details?id=com.freereels.app", page_id="d2",
                    first_seen_at=datetime.utcnow(), last_seen_at=datetime.utcnow(), channel="ads", network="meta")
        lonely_p = Product(product_code="PRD_T12", canonical_name="free reels")
        db.add(lonely_p)
        db.flush()
        lonely.product_id = lonely_p.id
        db.add(lonely)
        db.commit()
        ids = {"good": good.id, "bad": bad.id, "lonely": lonely.id, "prod": good.product_id, "lonely_p": lonely_p.id}

    purge_entertainment.main([])  # dry run changes nothing
    with SessionLocal() as db:
        assert db.get(Ad, ids["bad"]) is not None
    purge_entertainment.main(["--apply"])
    with SessionLocal() as db:
        assert db.get(Ad, ids["bad"]) is None and db.get(Ad, ids["lonely"]) is None
        assert db.get(Ad, ids["good"]) is not None and db.get(Product, ids["prod"]) is not None  # shared cluster kept
        assert db.get(Product, ids["lonely_p"]) is None  # cluster that only held drama: gone
        assert db.scalar(select(Creative).where(Creative.ad_id == ids["good"], Creative.source_url == "u3")).storage_key == "shared.mp4"
        assert "only-bad.mp4" in quiet and "shared.mp4" not in quiet  # shared file never deleted
        srcs = {(e.source, e.source_ad_id) for e in db.scalars(select(ContentExclusion).where(ContentExclusion.decision == "block"))}
        assert {("meta_library", "t11"), ("apify_meta", "x11")} <= srcs  # every provider id blocked → no comeback
    assert list(purge_entertainment.DATA_DIR.glob("backups/entertainment-*.json.gz"))  # backup written before delete
    with SessionLocal() as db:  # the other provider re-sends it: dropped
        r = AdRecord(source="apify_meta", source_ad_id="x11", platform="facebook", ad_text="anything")
        assert ingest.ingest_ads(db, [r])["excluded_entertainment"] == 1


# ---- TikTok Creative Center: its own industry label + empty rows (2026-10-14: TikTok data was mostly non-product)
def test_tiktok_non_product_industry_is_blocked_unless_the_ad_sells():
    trading = classify("Xtrend Speed", "Invest in 600+ assets", "Invest in 600+ assets", industry="label_20106000000", network="tiktok")
    assert trading[0] == "block" and trading[1] == ["industry:finance/trading"]
    sells = classify(None, "Gift set, 20% off", "COD and free shipping nationwide", industry="label_20106000000", network="tiktok")
    assert sells[0] == "keep"                                    # selling evidence outranks the industry label
    assert classify(None, "Hair serum", "Hair serum for dry hair", industry="label_14101000000", network="tiktok")[0] == "keep"


def test_empty_tiktok_row_is_blocked_but_an_empty_meta_ad_is_not():
    assert classify("ZODIAC SUPPLEMENTS", None, None, network="tiktok") == ("block", ["empty_tiktok_ad"])
    assert classify("Some Page", None, None, network="facebook")[0] == "keep"
    assert classify(None, None, None, url="https://shop.example.com/products/x", network="tiktok")[0] == "keep"


def test_ingest_stores_tiktok_labels_and_derives_funnel_from_objective(quiet):
    mk = lambda i, obj, ind="label_14103000000": AdRecord(
        source="tiktok_top_ads", source_ad_id=i, platform="tiktok", platforms=["tiktok"], country="PH", title="Whitening soap", ad_text="Whitening soap #fyp",
        raw_source={"industry_key": ind, "objective_key": f"campaign_objective_{obj}"})
    with SessionLocal() as db:
        out = ingest.ingest_ads(db, [mk("t1", "product_sales"), mk("t2", "lead_generation"), mk("t3", "traffic"), mk("t4", "conversion", "label_23112000000")])
        db.commit()
        ads = {a.external_id: a for a in db.scalars(select(Ad).where(Ad.source == "tiktok_top_ads"))}
    assert out["excluded_entertainment"] == 1 and "t4" not in ads            # drama industry, nothing sold
    assert (ads["t1"].funnel, ads["t1"].source_objective, ads["t1"].source_industry) == ("shop", "product_sales", "label_14103000000")
    assert ads["t2"].funnel == "form" and ads["t3"].funnel == "other"
