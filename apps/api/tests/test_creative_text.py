"""Wave / Creative multilingual evidence (services/creative_text.py, discovery.creative_signals, creative_enrich).
Rule under test: missing text lowers confidence, it never becomes a guessed score; OCR / transcript are spent only where needed."""
from types import SimpleNamespace

import pytest

from app import creative_enrich
from app.db import SessionLocal
from app.models import Ad, Creative, Product
from app.services import creative_text as T
from app.services import discovery


def ad(text=None, title=None, **kw):
    return Ad(id=kw.pop("id", 1), external_id="x", source="t", platform="facebook", ad_text=text, title=title, media_type="video",
              is_internal=False, **kw)  # transient: never added to a session


@pytest.mark.parametrize("text,lang", [
    ("Sinubukan ko ito at ang ganda ng resulta, para sa mga nanay", "tl"),
    ("هل تعلم سر هذا المنتج؟ جربت وشاهد النتيجة", "ar"),
    ("You will not believe what this is, get it now for your family", "en"),
    ("Bạn có biết bí mật của làn da đẹp không", "vi"),
    ("Shop now", "und"),
])
def test_detect_lang(text, lang):
    assert T.detect_lang(text)[0] == lang


def test_tagalog_and_arabic_signals_are_read():
    tl = "Alam mo ba ang sikreto? Sinubukan ko, panoorin ang resulta bago at pagkatapos para sa nanay ko"
    ar = "هل تعلم سر هذه الأداة؟ جربت وشاهد النتيجة قبل وبعد هدية لأمي"
    for t in (tl, ar):
        assert all(rx.search(t) for rx in (T.CURIOSITY, T.DEMO, T.TRANSFORM, T.UGC, T.EMOTION)), t
    assert not T.CURIOSITY.search("سرير جديد")  # "bed" is not "secret"


def test_short_caption_is_unknown_not_negative():
    e = T.ad_text_evidence(ad("Shop now"))
    assert e["reliability"] == 0 and T.needs_text(e)
    # a real caption in a language with a lexicon is fully readable
    full = T.ad_text_evidence(ad("Sinubukan ko ito at ang ganda ng resulta, panoorin mo ang video para sa mga nanay at anak"))
    assert full["reliability"] == 1 and not T.needs_text(full) and full["lang"] == "tl"
    # a language we cannot read counts for half
    assert T.ad_text_evidence(ad("这个产品真的很好用，我每天都用它，推荐给大家，质量非常好，价格也很便宜哦"))["reliability"] < 1


def test_ocr_and_transcript_fill_a_thin_caption():
    a = ad("Shop now", id=7)
    assert T.ad_text_evidence(a)["reliability"] == 0
    e = T.ad_text_evidence(a, ocr="Libreng shipping para sa lahat ng mga nanay", transcript="Sinubukan ko ito at ang ganda ng resulta")
    assert e["reliability"] > 0.5 and e["sources"] == ["caption", "ocr", "transcript"] and e["lang"] == "tl"


def test_no_readable_copy_gives_neutral_parts_and_zero_confidence():
    cs = discovery.creative_signals([ad("Shop now"), ad("Buy", id=2)], [])
    assert cs["text"]["coverage"] == 0
    p = Product(id=1, canonical_name="Thing", country="PH", saturation_score=0, confidence_score=0)  # transient
    f = {"markets": ["PH"], "active_ads": 2, "advertiser_count": 2}
    pv = discovery.potential_vector(p, f, [ad("Shop now"), ad("Buy", id=2)], [], [], SimpleNamespace(predict=lambda *a, **k: None, n_votes=0), {})
    assert pv["vector"]["wave_confidence"] == 0
    assert pv["wave_parts"]["scroll_stop_curiosity"] == pv["wave_parts"]["demo_strength"] == discovery.NEUTRAL_PART
    assert pv["creative_evidence"]["coverage"] == 0


# ------------------------------------------------------------ the job
@pytest.fixture
def seeded():
    with SessionLocal() as db:
        p = Product(product_code="CE-T1", canonical_name="Test Gadget", recommendation="TEST_NOW", opportunity_score=80)
        db.add(p)
        db.flush()
        a = Ad(external_id="ce1", source="t", platform="facebook", product_id=p.id, country="PH", ad_text="Shop now", is_active=True)
        db.add(a)
        db.flush()
        v = Creative(ad_id=a.id, product_id=p.id, type="video", source_url="u", status="stored", thumb_key="thumbs/x.jpg")
        db.add(v)
        db.commit()
        yield p.id, a.id, v.id
        db.query(Creative).filter(Creative.id == v.id).delete()
        db.query(Ad).filter(Ad.id == a.id).delete()
        db.query(Product).filter(Product.id == p.id).delete()
        db.commit()


def test_dry_run_finds_the_thin_ad_and_calls_nothing(seeded, monkeypatch):
    monkeypatch.setattr(creative_enrich, "ocr_image", lambda *a: pytest.fail("dry run must not OCR"))
    with SessionLocal() as db:
        assert creative_enrich.tick(db, dry_run=True)["candidates"] == 1


def test_ocr_then_transcript_only_when_still_unreadable(seeded, monkeypatch):
    _, ad_id, cid = seeded
    monkeypatch.setattr(creative_enrich, "store", lambda: SimpleNamespace(get=lambda k: b"jpg"))
    calls = []
    monkeypatch.setattr(creative_enrich, "ocr_image", lambda data, a: (calls.append("ocr") or "SALE", "tesseract"))  # 1 word: still thin
    monkeypatch.setattr(creative_enrich, "transcribe", lambda c: calls.append("tr") or "Sinubukan ko ito at ang ganda ng resulta para sa nanay ko")
    with SessionLocal() as db:
        r = creative_enrich.tick(db)
        assert calls == ["ocr", "tr"] and r["ocr"] == 1 and r["transcripts"] == 1
        c = db.get(Creative, cid)
        assert c.ocr_text == "SALE" and c.transcript.startswith("Sinubukan")
        assert creative_enrich.candidates(db) == []  # readable now → never spent on again


def test_no_engine_records_nothing_so_a_later_tick_retries(seeded, monkeypatch):
    _, _, cid = seeded
    monkeypatch.setattr(creative_enrich, "store", lambda: SimpleNamespace(get=lambda k: b"jpg"))
    monkeypatch.setattr(creative_enrich, "ocr_image", lambda data, a: (None, "none"))
    monkeypatch.setattr(creative_enrich, "transcribe", lambda c: None)
    with SessionLocal() as db:
        r = creative_enrich.tick(db)
        assert r["ocr"] == 0 and "unavailable" in r["skipped"] and db.get(Creative, cid).ocr_text is None
