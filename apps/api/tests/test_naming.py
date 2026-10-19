"""Product naming + multilingual search regressions (docs/PRODUCT_NAMING.md §6). Throw-away SQLite (conftest), raw
batches and media downloads stubbed, no model call (GEMINI_API_KEY blanked)."""
import pytest
from sqlalchemy import func, select

from app import backfill_names, ingest, media
from app.collector.contract import AdRecord, MediaItem
from app.db import SessionLocal
from app.models import Ad, Creative, Product
from app.routers.intel import search
from app.services import discovery, lexicon, naming
from app.services.entity_resolution import merge_products, split_ads


@pytest.fixture
def quiet(monkeypatch):
    monkeypatch.setattr(ingest, "write_raw_batch", lambda *a, **k: None)
    monkeypatch.setattr(media, "enqueue", lambda *a, **k: None)


def rec(i, adv=None, text=None, title=None, url=None, channel_platform="facebook", **kw):
    return AdRecord(source="test_naming", source_ad_id=f"n{i}", platform=channel_platform, country="PH", advertiser=adv,
                    title=title, ad_text=text, landing_page=url, product_url=url, active=True,
                    media=[MediaItem(type="image", url=f"https://cdn.example/n{i}.jpg")], **kw)


def product_of(db, i) -> Product:
    return db.get(Product, db.scalar(select(Ad.product_id).where(Ad.source == "test_naming", Ad.external_id == f"n{i}")))


# ---------------------------------------------------------------- 1. never a CTA / shop / advertiser / platform
@pytest.mark.parametrize("title,adv", [
    ("Click Order Now", "Some Page"), ("TikTok Shop", "TikTok Shop"), ("Blue Light Shopping", "Blue Light Shopping"),
    ("Shop now", None), ("Free Shipping Free Returns", "X"), ("FLASH SALE 50% OFF", "Y"), ("Mua ngay kẻo hết", None),
    ("Shopee", "Shopee"), ("Firm Body or Money Back!", "Sculpiflex"), ("اطلب الآن", None),
])
def test_cta_shop_platform_is_never_a_name(title, adv):
    assert naming.best({"title": title, "ad_text": title, "advertiser": adv})["name"] is None


def test_evidence_order_and_status():
    listing = naming.best({"channel": "commerce", "title": "New Hot Sale Lucky Pixiu Bracelet Obsidian Beads Men Women"})
    assert listing["name"] == "Lucky Pixiu Bracelet Obsidian Beads Men Women" and listing["status"] == "AUTO_CONFIRMED"
    slug = naming.best({"title": "50% OFF today", "landing_url": "https://shop.example/products/neck-massager-pro"})
    assert (slug["name"], slug["source"], slug["status"]) == ("Neck Massager Pro", "slug", "AUTO_CONFIRMED")
    copy = naming.best({"title": "Discover Affordable Universal Stylus Pen for your Device! #styluspen"})
    assert copy["name"] == "Affordable Universal Stylus Pen" and copy["status"] == "SUGGESTED"  # copy = needs MKT confirm


def test_ingest_without_evidence_is_unknown_not_shop_name(quiet):
    with SessionLocal() as db:
        ingest.ingest_ads(db, [rec(1, adv="TikTok Shop", title="Click Order Now", text="Click Order Now")])
        p = product_of(db, 1)
        assert p.name_status == "UNKNOWN" and "tiktok" not in p.canonical_name.lower()
        assert naming.card_fields(p)["name"] == "Quảng cáo từ TikTok Shop" and p.canonical_name == naming.UNKNOWN_LABEL
        assert db.scalar(select(Ad.ad_type).where(Ad.external_id == "n1")) in ("BRAND_AD", "UNKNOWN")


# ---------------------------------------------------------------- 2. nothing invented
def test_model_name_must_use_the_ads_own_words():
    ad = {"title": "Firm Body or Money Back", "ad_text": "Firm Body or Money Back!", "landing_url": "https://quiz.example.com/"}
    assert naming.best({**ad, "llm_name": "Posture Pro Belt"})["name"] is None  # not in the ad → dropped
    ok = naming.best({"ad_text": "sobrang saya ko sa bagong turbo fan na to! may ilaw at powerbank", "llm_name": "Portable Turbo Fan"})
    assert ok["name"] == "Portable Turbo Fan" and ok["status"] == "SUGGESTED"


# ---------------------------------------------------------------- 3. MKT names are locked
def test_manual_name_survives_crawler_and_undo_restores(quiet):
    with SessionLocal() as db:
        ingest.ingest_ads(db, [rec(10, adv="Shop A", title="Lucky Charm Pixiu Bracelet")])
        p = product_of(db, 10)
        before = p.canonical_name
        naming.set_name(p, "Pixiu Bracelet", "Vòng tay tỳ hưu", "mkt_lan")
        db.commit()
        # a better-evidenced ad for the same product arrives (product page) — must not rename a VERIFIED product
        ingest.ingest_ads(db, [rec(11, adv="Shop B", url="https://pixiu.example/products/lucky-charm-pixiu-bracelet-gold")])
        db.refresh(p)
        assert (p.canonical_name, p.name_vi, p.name_status, p.manual_override) == ("Pixiu Bracelet", "Vòng tay tỳ hưu", "VERIFIED", True)
        assert naming.undo(p) and p.canonical_name == before and not p.manual_override


# ---------------------------------------------------------------- 4. no wrong merges
def test_same_advertiser_different_products_stay_apart(quiet):
    with SessionLocal() as db:
        ingest.ingest_ads(db, [rec(20, adv="Mega Deals", url="https://megadeals.example/products/pixiu-obsidian-bracelet"),
                               rec(21, adv="Mega Deals", url="https://megadeals.example/products/electric-massage-gun"),
                               rec(22, adv="Mega Deals", text="Hello po! Message us for details"),
                               rec(23, adv="Mega Deals", text="Thank you for 10k followers mga ka-deals")])
        ids = {product_of(db, i).id for i in (20, 21, 22, 23)}
        assert len(ids) == 4  # two named products + two unidentified ads: never one "Mega Deals" bucket


def test_same_product_page_groups_without_a_name(quiet):
    with SessionLocal() as db:
        url = "https://relief.example/products/rw3"  # slug too short to name it, still one product page
        ingest.ingest_ads(db, [rec(30, adv="Relief", text="It is not hype, it is the first device with 16 nodes", url=url),
                               rec(31, adv="Relief", text="Crushed leg day? Do not spend 3 days limping", url=url)])
        assert product_of(db, 30).id == product_of(db, 31).id


# ---------------------------------------------------------------- 5. no ad lost by re-clustering / merge / split
def test_recluster_split_merge_keep_every_ad(quiet):
    with SessionLocal() as db:
        ingest.ingest_ads(db, [rec(40 + i, adv="Bucket Page", text=t, url=u) for i, (t, u) in enumerate([
            ("Order now!!", None), ("Best deal today", None),
            (None, "https://bp.example/products/jade-pixiu-pendant"), (None, "https://bp.example/products/knee-brace-sport")])])
        total = db.scalar(select(func.count(Ad.id)))
        bucket = Product(product_code="PRD_BUCKET", canonical_name="Bucket Page (chưa đặt tên)")
        db.add(bucket)
        db.flush()
        ad_ids = list(db.scalars(select(Ad.id).where(Ad.external_id.in_([f"n{40 + i}" for i in range(4)]))))
        backfill_names.move_ads(db, ad_ids, bucket.id)
        ads = list(db.scalars(select(Ad).where(Ad.id.in_(ad_ids))))
        backfill_names.recluster(db, bucket, ads, {})
        db.flush()
        assert db.scalar(select(func.count(Ad.id))) == total and db.scalar(select(func.count(Ad.id)).where(Ad.product_id.is_(None))) == 0
        named = {product_of(db, 42).canonical_name, product_of(db, 43).canonical_name}
        assert named == {"Jade Pixiu Pendant", "Knee Brace Sport"}
        # creatives follow their ad
        assert all(c.product_id == product_of(db, 42).id for c in db.scalars(select(Creative).where(Creative.ad_id == ad_ids[2])))
        a, b = product_of(db, 42), product_of(db, 43)
        merge_products(db, b, a, by="t")
        assert product_of(db, 43).id == a.id and db.scalar(select(func.count(Ad.id))) == total
        new = split_ads(db, a, [ad_ids[3]], "Knee Brace", "t")
        assert product_of(db, 43).id == new.id and new.name_status == "VERIFIED"
        db.rollback()


# ---------------------------------------------------------------- 6. multilingual + multi-keyword search
def test_lexicon_expansion():
    e = lexicon.expand("Vòng tay phong thủy")
    assert set(e["concepts"]) == {"bracelet", "feng shui"} and "feng shui" in e["groups"][1] and "lucky" in e["related"][1]
    assert lexicon.english_queries("vòng tay phong thủy")[:2] == ["feng shui bracelet", "lucky bracelet"]
    assert {c for _, c in lexicon.concepts_in("pulseras swerte")} == {"bracelet", "lucky"}
    assert {c for _, c in lexicon.concepts_in("سوار حظ")} == {"bracelet", "lucky"}


def test_search_finds_translations_and_ranks_exact_first(quiet):
    with SessionLocal() as db:
        ingest.ingest_ads(db, [
            rec(60, adv="FS Shop", url="https://fs.example/products/feng-shui-bracelet-black-obsidian"),
            rec(61, adv="Luck PH", url="https://luck.example/products/lucky-fortune-bracelet"),
            rec(62, adv="VN Shop", title="Vòng tay phong thủy đá thạch anh", text="Vòng tay phong thủy đá thạch anh"),
            rec(63, adv="Gun", url="https://gun.example/products/deep-tissue-massage-gun"),
            rec(64, adv="Spring", url="https://spring.example/products/spring-water-bottle"),
        ])
        for a in db.scalars(select(Ad).where(Ad.source == "test_naming")):
            a.in_target = True
        db.commit()
        pid = {i: product_of(db, i).id for i in (60, 61, 62, 63, 64)}
        res = search(q="Vòng tay phong thủy", country="PH", active_only=False, db=db, limit=50)
        tier = {r["id"]: r["match_tier"] for r in res["rows"]}
        assert tier.get(pid[60]) == "synonym" and tier.get(pid[61]) == "related" and pid[63] not in tier
        assert res["rows"][0]["id"] == pid[62] and res["rows"][0]["match_tier"] == "exact"  # the Vietnamese ad itself first
        multi = search(q="fortune | massage gun", country="PH", active_only=False, db=db, limit=50)
        assert {pid[61], pid[63]} <= {r["id"] for r in multi["rows"]}
        ring = search(q="nhẫn", country="PH", active_only=False, db=db, limit=50)  # → "ring": must not match "spring"
        assert pid[64] not in {r["id"] for r in ring["rows"]}
        unknown = search(q=None, country="PH", active_only=False, name_status="UNKNOWN", db=db, limit=200)
        assert all(r["name_status"] == "UNKNOWN" for r in unknown["rows"])


# ---------------------------------------------------------------- 7. Opp Score v2 gate reads the status, not the string
def test_opportunity_gate_uses_name_status():
    p = Product(canonical_name="Ems Neck Massager", category="health", name_status="AUTO_CONFIRMED")
    f = {"active_ads": 5, "advertiser_count": 3}
    assert discovery.evidence(p, f, [])["unnamed"] is False
    p.name_status = "UNKNOWN"
    assert discovery.evidence(p, f, [])["unnamed"] is True
    legacy = Product(canonical_name="Acme (chưa đặt tên)", name_status=None)
    assert discovery.evidence(legacy, f, [])["unnamed"] is True
    v = {"market_demand": 60, "momentum": 30, "compliance_risk": 0, "competition": 20, "market_fit": 70}
    assert discovery.discovery_decision(v, "BREAKOUT", {**discovery.evidence(p, f, []), "longevity_days": 30})[0] == "WATCH"


# ---------------------------------------------------------------- 8. shop-based provisional label (display only)
def test_provisional_shop_label_is_display_only(quiet):
    with SessionLocal() as db:
        ingest.ingest_ads(db, [rec(80, adv="Acme Home Store", text="Hello po! Message us for details"),
                               rec(81, adv="TikTok Shop", text="Click Order Now")])
        a, t = product_of(db, 80), product_of(db, 81)
        ca, ct = naming.card_fields(a), naming.card_fields(t)
        assert ca["name"] == "Sản phẩm từ Acme Home Store" and ca["name_provisional"] and ca["name_status"] == "UNKNOWN"
        assert a.canonical_name == naming.UNKNOWN_LABEL and ca["canonical_name"] is None  # never the canonical name
        assert ct["name"] == "Quảng cáo từ TikTok Shop" and ct["name_provisional"]  # platform: "ad from", never "product from"
        assert t.canonical_name == naming.UNKNOWN_LABEL
        assert discovery.evidence(a, {"active_ads": 3}, [])["unnamed"] is True  # Opp Score gate unchanged
        for q in ("acme home store", "sản phẩm từ"):  # product-name search never sees it
            for a_ in db.scalars(select(Ad).where(Ad.source == "test_naming", Ad.external_id == "n80")):
                a_.in_target = True
            db.commit()
            ids = {r["id"] for r in search(q=q, country="PH", active_only=False, db=db, limit=50, expand=False)["rows"] if r["matched_ads"] == 0}
            assert a.id not in ids
        # a second, different product of the same shop is its own product — never merged under the shop name
        ingest.ingest_ads(db, [rec(82, adv="Acme Home Store", url="https://acme.example/products/acme-bamboo-cutting-board")])
        assert product_of(db, 82).id != a.id and product_of(db, 82).canonical_name == "Acme Bamboo Cutting Board"
