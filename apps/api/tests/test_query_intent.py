"""Search in any language: keyword meaning (lexicon → cached model answer), live-job relevance, no keyword tagging of fuzzy hits.
Throw-away SQLite (conftest); the model is stubbed — no API call."""
import pytest
import os
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import ingest, media
from app.collector.contract import AdRecord, MediaItem
from app.db import SessionLocal
from app.main import app
from app.models import Ad, Product, QueryIntent
from app.services import lexicon, llm, query_intent


@pytest.fixture(autouse=True)
def admin_api(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "query-regression-admin")


def authorized_client():
    return TestClient(app, headers={"X-Admin-Token": os.environ["ADMIN_TOKEN"]})


@pytest.fixture
def db():
    with SessionLocal() as s:
        s.query(QueryIntent).delete()
        s.commit()
        query_intent._mem.clear()
        query_intent._spent.update(day=None, n=0)
        yield s


def fake_model(monkeypatch, answer, calls):
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "complete_json", lambda *a, **k: calls.append(a[1]) or answer)


# ---------------------------------------------------------------- the dictionary
@pytest.mark.parametrize("q,concept", [("kính mắt", "eyeglasses"), ("kính cận", "eyeglasses"), ("glasses", "eyeglasses"), ("眼镜", "eyeglasses"),
                                       ("نظارات طبية", "eyeglasses"), ("kính râm", "sunglasses"), ("kính mát", "sunglasses"), ("墨镜", "sunglasses")])
def test_eyeglasses_and_sunglasses_are_different_concepts_in_every_language(q, concept):
    assert lexicon.expand(q)["concepts"] == [concept]


# ---------------------------------------------------------------- a keyword the dictionary cannot read
ANSWER = {"is_product": True, "product": "Makeup Organizer", "variants": ["makeup organizer", "organizador de maquillaje", "cosmetic storage", "hộp đựng mỹ phẩm", "منظم مكياج"],
          "related": ["vanity tray"], "search_terms": {"en": "makeup organizer", "tl": "lalagyan ng make up", "ar": "منظم مكياج"}}


def test_unknown_keyword_is_asked_once_then_cached_for_good(db, monkeypatch):
    calls = []
    fake_model(monkeypatch, ANSWER, calls)
    e = query_intent.expand(db, "organizador de maquillaje")
    assert e["source"] == "llm" and "cosmetic storage" in e["groups"][0] and "organizador de maquillaje" in e["groups"][0]
    db.commit()
    query_intent._mem.clear()   # a new process: the table answers, the model is not asked again
    assert query_intent.expand(db, "Organizador de Maquillaje")["source"] == "llm" and len(calls) == 1
    assert db.get(QueryIntent, lexicon.fold("organizador de maquillaje")).source == "llm"


def test_not_a_product_is_remembered_and_never_guessed(db, monkeypatch):
    calls = []
    fake_model(monkeypatch, {"is_product": False, "product": "", "variants": [], "related": [], "search_terms": {}}, calls)
    e = query_intent.expand(db, "hello there my friend")
    assert e["source"] == "none" and e["groups"] == []
    query_intent.expand(db, "hello there my friend")
    assert len(calls) == 1


def test_no_model_means_the_keyword_as_written_and_nothing_cached(db, monkeypatch):
    monkeypatch.setattr(llm, "available", lambda: False)
    e = query_intent.expand(db, "organizador de maquillaje")
    assert e["source"] == "none" and db.get(QueryIntent, lexicon.fold("organizador de maquillaje")) is None


def test_daily_budget_stops_the_model(db, monkeypatch):
    calls = []
    fake_model(monkeypatch, ANSWER, calls)
    monkeypatch.setattr(query_intent, "DAILY_CALLS", 1)
    query_intent.expand(db, "organizador de maquillaje")
    assert query_intent.expand(db, "caja para joyas")["source"] == "none" and len(calls) == 1


def test_live_crawl_keywords_follow_the_market_language(db, monkeypatch):
    fake_model(monkeypatch, ANSWER, [])
    assert query_intent.source_queries(db, "organizador de maquillaje", ["PH"]) == ["makeup organizer", "lalagyan ng make up"]
    assert query_intent.source_queries(db, "kính mắt", ["PH"])[0] == "eyeglasses"   # the dictionary still answers its own concepts


# ---------------------------------------------------------------- results must be about the keyword
def mk(db, name, text, ext, advertiser="Shop"):
    p = Product(product_code=f"QI-{ext}", canonical_name=name, name_status="SUGGESTED")
    db.add(p)
    db.flush()
    db.add(Ad(external_id=f"qi{ext}", source="t", platform="facebook", network="meta", product_id=p.id, ad_text=text, is_active=True,
              search_text=f"{name} {text} {advertiser}".lower()))
    db.flush()
    return p.id


def test_live_job_hides_fuzzy_source_hits_and_counts_them(db):
    a = mk(db, "Gọng kính cận thời trang", "Kính mắt cận thị nam nữ, gọng nhẹ", 1)
    b = mk(db, "Blue light glasses", "Anti blue light glasses for gamers", 2)
    c = mk(db, "Sản phẩm từ NHÔM KÍNH GIÁ RẺ", "Cửa nhôm kính giá rẻ, thi công nhanh", 3)
    d = mk(db, "Mặt nạ phòng độc", "Mặt nạ chống độc, bảo vệ mắt", 4)
    db.commit()
    ids = f"{a},{b},{c},{d}"
    cl = authorized_client()
    r = cl.get("/api/search", params={"q": "kính mắt", "ids": ids}).json()
    assert {x["id"] for x in r["rows"]} == {a, b} and r["off_topic"] == 2   # eyeglasses in vi and in en; door + mask are off-topic
    assert next(x for x in r["rows"] if x["id"] == a)["match_tier"] == "exact" and next(x for x in r["rows"] if x["id"] == b)["match_tier"] == "synonym"
    assert r["understood"]["kính mắt"]["source"] == "lexicon"
    r = cl.get("/api/search", params={"q": "kính mắt", "ids": ids, "relevant": "false"}).json()
    assert {x["id"] for x in r["rows"]} == {a, b, c, d}
    r = cl.get("/api/search", params={"q": "kính mắt", "ids": ids, "only_off": "true"}).json()
    assert {x["id"] for x in r["rows"]} == {c, d} and r["total"] == 2   # the section under the list: exactly what `relevant` hides
    assert cl.get("/api/search", params={"q": "kính mắt", "ids": f"{a},{b}", "only_off": "true"}).json()["rows"] == []


# ---------------------------------------------------------------- a source's keyword is not the ad's text
@pytest.fixture
def quiet(monkeypatch):
    monkeypatch.setattr(ingest, "write_raw_batch", lambda *a, **k: None)
    monkeypatch.setattr(media, "enqueue", lambda *a, **k: None)


def rec(i, platform, adv, text):
    return AdRecord(source=f"qi_{platform}", source_ad_id=f"q{i}", platform=platform, country="PH", advertiser=adv, ad_text=text, active=True,
                    matched_query="kính mắt", media=[MediaItem(type="image", url=f"https://cdn.example/q{i}.jpg")])


def test_a_sources_loose_match_never_tags_the_ad_with_the_keyword(db, quiet):
    ingest.ingest_ads(db, [rec(1, "facebook", "Cửa nhôm Hưng Phát", "Cửa nhôm kính giá rẻ, thi công nhanh toàn quốc")])
    ingest.ingest_ads(db, [rec(2, "aliexpress", "Factory A", "NH35 NH36 Machine Movement Spacer Ring Fits NH38 watch parts")])
    for ext in ("q1", "q2"):
        assert "kính mắt" not in db.scalar(select(Ad).where(Ad.external_id == ext)).search_text   # its own text decides, not the source's guess


def test_chinese_listing_is_found_by_its_own_words_through_the_translation(db):
    p = mk(db, "新款近视眼镜男防蓝光", "新款近视眼镜男防蓝光 平光镜 批发", 7)
    db.commit()
    r = authorized_client().get("/api/search", params={"q": "kính mắt", "ids": str(p)}).json()
    assert [x["id"] for x in r["rows"]] == [p] and r["rows"][0]["match_tier"] == "synonym"


def test_sunglasses_rank_below_eyeglasses_for_an_eyeglasses_keyword(db):
    eye = mk(db, "Round Myopia Glasses Ultralight", "Round myopia glasses, light frame", 20)
    sun = mk(db, "Retro Square Sunglasses Men", "Retro sunglasses polarized UV400", 21)
    db.commit()
    rows = authorized_client().get("/api/search", params={"q": "kính mắt", "ids": f"{eye},{sun}"}).json()["rows"]
    tier = {r["id"]: r["match_tier"] for r in rows}
    assert tier[eye] == "synonym" and tier[sun] == "related" and [r["id"] for r in rows][0] == eye


def test_a_failing_model_is_asked_once_with_a_short_timeout_then_left_alone(db, monkeypatch):
    """Audit 2026-10-14: a 429 held /api/ads?q= for ~20 s (3 retries inside the request) and was asked again on every search."""
    seen = []
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "complete_json", lambda *a, **k: seen.append(k) or None)
    query_intent._failed.clear()
    assert query_intent.expand(db, "organizador de maquillaje")["source"] == "none"
    assert query_intent.expand(db, "organizador de maquillaje")["source"] == "none"
    assert len(seen) == 1 and seen[0]["retries"] == 1 and seen[0]["timeout"] <= 10
