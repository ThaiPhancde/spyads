"""Offline regressions: identity, free sources, fallback, timestamps and auto-spend limits."""
from datetime import datetime, timedelta

import numpy as np
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Product, Ad, Creative
from app.collector.contract import AdRecord
from app.pricing import router, jobs, quota
from app.pricing.identity import matched, local_sources
from app.pricing.models import PriceObservation, PriceJob, VERIFIED_MARKETPLACE, MARKET_REFERENCE
from app.services import naming, entity_resolution
from app.services.product_identity import metadata, identity_match
from app.visual.models import ProductImage


@pytest.fixture
def db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path}/prices.db")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(jobs, "SessionLocal", factory)
    monkeypatch.setattr(quota, "SessionLocal", factory)
    monkeypatch.setenv("PRICE_AUTO_TEST_ENABLED", "0")
    monkeypatch.setattr(router, "rung_lens", lambda *a, **kw: [])
    def unexpected(*a, **kw):
        raise AssertionError("Unexpected provider call")
    monkeypatch.setattr(router.extract, "extract", unexpected)
    monkeypatch.setattr(router.brightdata, "search", unexpected)
    monkeypatch.setattr(router.shopping, "search", unexpected)
    entity_resolution._idx.update(pid=0, aid=0, names={}, inv={})
    with factory() as session:
        yield session
    engine.dispose()


def make_product(db, code="p", **kw):
    p = Product(product_code=code, canonical_name="Neck massager", country="PH", recommendation="TEST", features={}, **kw)
    db.add(p)
    db.flush()
    return p


def make_ad(db, p, key="ad", **kw):
    a = Ad(**(dict(external_id=key, source="test", platform="facebook", network="meta", channel="ads",
                  product_id=p.id, country="PH", funnel="mess", first_seen_at=datetime.utcnow()-timedelta(days=30),
                  last_seen_at=datetime.utcnow(), is_internal=False) | kw))
    db.add(a)
    db.flush()
    return a


def test_query_preserves_models_and_removes_only_prices():
    assert router.query_text("iPhone 16 case 128GB ₱999") == "iPhone 16 case 128GB"
    assert router.query_text("TRON X200 headphones 1290 PHP") == "TRON X200 headphones"
    assert not matched("iPhone 16 case", "iPhone 15 case")
    assert matched("iPhone 16 case", "Apple iPhone 16 protective case")
    assert not matched("TRON X200 headphones", "TRON X100 headphones")


def test_card_does_not_label_usd_average_as_php():
    from app.main import product_card as main_card
    from app.routers.intel import product_card as media_card
    p = Product(id=1, canonical_name="Demo", currency="PHP", confidence_score=50, features={"avg_price": 16.5}, potential={})
    for card in (main_card, media_card):
        assert card(p)["price"] == 16.5 and card(p)["currency"] == "USD"
    p.price = 899
    for card in (main_card, media_card):
        assert card(p)["price"] == 899 and card(p)["currency"] == "PHP"


def test_verified_cache_feeds_margin_but_unverified_reference_does_not():
    from app.main import mkt_summary
    from app.pricing.models import VERIFIED_COMPETITOR
    now = datetime.utcnow()
    def observation(ptype, amount, role=None):
        return PriceObservation(price_type=ptype, price_amount=amount, currency="USD", observed_at=now,
                                expires_at=now+timedelta(days=1), price_confidence=.9, match_confidence=1,
                                evidence={"role": role})
    verified = [observation(VERIFIED_COMPETITOR, 20), observation(VERIFIED_MARKETPLACE, 5, "supplier")]
    result = mkt_summary([], {}, verified)
    assert result["sell_usd"] == 20 and result["supplier_usd"] == 5 and result["margin_est"] == .6
    assert mkt_summary([], {}, [observation(MARKET_REFERENCE, 20), observation(VERIFIED_MARKETPLACE, 5)])["margin_est"] is None
    verified[0].expires_at = now-timedelta(hours=1)
    assert mkt_summary([], {}, verified)["margin_est"] is None


def test_identity_metadata_not_retailer_sku_alone():
    assert identity_match({"sku": "123"}, {"sku": "123"}) is None
    assert identity_match({"brand": "Sony", "sku": "X-200"}, {"brand": "sony", "sku": "X200"}) is True
    assert identity_match({"gtin": "123456"}, {"gtin": "234567"}) is False
    assert identity_match({"gtin": "123456"}, {"gtin": "123456"}) is None
    assert identity_match({"gtin": "4006381333931"}, {"gtin": "4006381333931"}) is True
    rec = AdRecord(source="test", source_ad_id="1", raw_source={"product": {"product_title": "Neck massager", "sku": "M-200", "brand": "TRON"}})
    md = metadata(rec)
    assert md["sku"] == "M-200"
    assert naming.best({"metadata": md})["name"].casefold() == "neck massager"


def test_cluster_rejects_wrong_model_and_matches_grounded_identity(db):
    p = make_product(db, product_identity={"brand": "TRON", "sku": "M200"})
    hit, score = entity_resolution.find_best_match(db, {"name": "Cervical massager", "identity": {"brand": "TRON", "sku": "M200"}})
    assert hit.id == p.id and score == 1
    assert entity_resolution.match_score({"name": "iPhone 16 case"}, Product(canonical_name="iPhone 15 case"), []) == 0


def test_free_cluster_source_price_stops_all_paid_providers(db):
    p = make_product(db)
    source = make_ad(db, p, "supplier", channel="commerce", network="aliexpress", price=9.5, currency="USD", landing_url="https://aliexpress.com/item/123.html")
    db.commit()
    steps = router.Step()
    obs = router.resolve(db, f"product:{p.id}", steps, paid=True)
    assert obs[0].price_type == VERIFIED_MARKETPLACE and obs[0].price_amount == 9.5
    assert obs[0].evidence["role"] == "supplier" and obs[0].observed_at == source.last_seen_at
    assert [s["step"] for s in steps.rows] == ["local_marketplace"]


def test_cached_price_keeps_original_age_no_network(db):
    p = make_product(db)
    now = datetime.utcnow()
    price = PriceObservation(subject=f"product:{p.id}", cluster_key=f"product:{p.id}", price_type=MARKET_REFERENCE,
                             provider="test", price_amount=899, price_low=899, price_high=899, currency="PHP", market_country="PH",
                             observed_at=now-timedelta(days=2), expires_at=now+timedelta(days=2), price_confidence=.6, match_confidence=.8)
    db.add(price)
    db.commit()
    result = router.resolve(db, price.subject, router.Step(), paid=True)[0]
    assert result.observed_at == price.observed_at and result.expires_at == price.expires_at


def test_cached_clip_supports_free_source_match_without_inference(db):
    p = make_product(db, "customer")
    source = make_product(db, "source")
    a = make_ad(db, p, "consumer")
    b = make_ad(db, source, "supplier", channel="commerce", price=9, currency="USD")
    for i, owner in enumerate([a, b]):
        c = Creative(ad_id=owner.id, product_id=owner.product_id, type="image", source_url=f"https://cdn.test/{i}.jpg")
        db.add(c)
        db.flush()
        v = np.array([1, 0], dtype=np.float16).tobytes()
        db.add(ProductImage(source="creative", source_id=c.id, phash=["aaaaaaaaaaaaaaaa", "5555555555555555"][i], embedding=v, emb_model="clip"))
    db.commit()
    found = local_sources(db, p, [a])
    assert found and found[0][0].id == b.id and found[0][1] == "cached_visual"


def test_empty_brightdata_success_falls_back_to_shopping_and_verifies_page(db, monkeypatch):
    monkeypatch.setattr(router.brightdata, "search", lambda *a: [])
    monkeypatch.setattr(router.shopping, "unavailable", lambda: None)
    monkeypatch.setattr(router.shopping, "search", lambda *a: [{"title": "Neck massager", "price": 899, "currency": "PHP", "seller": "Shop", "link": "https://shop.test/products/massager"}])
    monkeypatch.setattr(router.extract, "extract", lambda url: {"price": 899, "currency": "PHP", "confidence": .9, "title": "Neck massager", "url": url, "method": "jsonld"})
    steps = router.Step()
    out = router.rung_reference("product:1", "product:1", "PH", "Neck massager", steps)
    assert any(o.price_type == VERIFIED_MARKETPLACE for o in out)
    assert any(s["step"] == "serpapi_shopping" for s in steps.rows)


def test_wrong_product_page_is_rejected_even_if_search_title_matched(db, monkeypatch):
    monkeypatch.setattr(router.extract, "extract", lambda url: {"price": 899, "currency": "PHP", "confidence": .9, "title": "iPhone 15 case", "url": url, "method": "jsonld"})
    assert router.rung_page("product:1", "product:1", "PH", "https://shop.test/p", False, router.Step(), expected_query="iPhone 16 case") is None


def test_auto_budget_is_test_only_deduped_and_bounded(db, monkeypatch):
    monkeypatch.setenv("PRICE_AUTO_TEST_ENABLED", "1")
    monkeypatch.setenv("PRICE_AUTO_DAILY_LIMIT", "1")
    p = make_product(db, "test")
    watch = make_product(db, "watch")
    watch.recommendation = "WATCH"
    second = make_product(db, "second")
    a = make_ad(db, p)
    db.commit()
    assert not jobs._auto_paid(f"product:{watch.id}")
    assert jobs._auto_paid(f"ad:{a.id}")
    assert not jobs._auto_paid(f"product:{p.id}")  # same cluster, no duplicate spend
    assert not jobs._auto_paid(f"product:{second.id}")


def test_queue_does_not_negative_cache_running_lens(db, monkeypatch):
    p = make_product(db)
    j = PriceJob(subject=f"product:{p.id}", status="running", attempts=1)
    db.add(j)
    db.commit()
    def pending(db, subject, steps, paid=False):
        steps.add("serpapi_lens", False, "lens_pending", job_id=123)
        return []
    monkeypatch.setattr(jobs, "resolve", pending)
    assert jobs.run_one(j.id)["pending"]
    db.expire_all()
    assert db.get(PriceJob, j.id).status == "queued"
    assert not list(db.scalars(select(PriceObservation)))
