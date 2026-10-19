"""Naming queue (naming_ai.py) + free-first LLM chain (llm_providers.py). Throw-away SQLite; providers are a fake transport."""
import json
import uuid
from datetime import datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from app import naming_ai
from app.db import SessionLocal
from app.models import Ad, Advertiser, LlmProviderState, Product
from app.services import llm, llm_providers as P
from app.visual.models import ExternalApiCall

RUN = uuid.uuid4().hex[:6]
_n = {"i": 0}


def uniq():
    _n["i"] += 1
    return _n["i"]


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    monkeypatch.setenv("CEREBRAS_API_KEY", "csk_test")
    monkeypatch.setenv("LLM_PROVIDERS", "groq,cerebras")
    monkeypatch.setattr(llm, "_disabled", False)
    with SessionLocal() as s:
        s.query(LlmProviderState).delete()
        s.query(ExternalApiCall).filter(ExternalApiCall.provider.like("llm_%")).delete(synchronize_session=False)
        s.commit()
        P._mem.clear()
        P._mem_at["t"] = 0
        yield s
        s.rollback()


def product(db, ads=(), status="UNKNOWN", name=None, manual=None, checked=True):
    p = Product(product_code=f"NA-{RUN}-{uniq()}", canonical_name=name or N_UNKNOWN, name_status=status, manual_override=manual, in_target=True,
                naming_checked_at=datetime.utcnow() if checked else None)
    db.add(p)
    db.flush()
    for kw in ads:
        db.add(Ad(external_id=f"na{RUN}-{uniq()}", source="t", platform="facebook", product_id=p.id, is_active=True, first_seen_at=datetime(2026, 10, 1), **kw))
    db.flush()
    return p


N_UNKNOWN = "Chưa xác định sản phẩm"
GROQ_HDR = {"x-ratelimit-limit-requests": "1000", "x-ratelimit-remaining-requests": "990", "x-ratelimit-reset-requests": "1m26.4s",
            "x-ratelimit-limit-tokens": "8000", "x-ratelimit-remaining-tokens": "7000", "x-ratelimit-reset-tokens": "3.5s"}


def fake_http(monkeypatch, handler):
    calls = []

    def post(url, headers=None, json=None, timeout=None):
        calls.append({"url": url, "body": json})
        return handler(url, json)

    monkeypatch.setattr(P.httpx, "post", post)
    return calls


def answer(results, headers=GROQ_HDR, usage=None):
    return httpx.Response(200, headers=headers, json={"choices": [{"message": {"content": json.dumps({"results": results})}}],
                                                      "usage": usage or {"total_tokens": 900}})


# ---------------------------------------------------------------- headers, cooldown, fallback
def test_rate_limit_headers_of_each_provider_are_normalised():
    g = P.parse_limits("groq", GROQ_HDR)
    assert g["requests_day"] == {"limit": 1000, "remaining": 990, "reset_s": 86.4} and g["tokens_minute"]["reset_s"] == 3.5
    c = P.parse_limits("cerebras", {"x-ratelimit-remaining-requests-day": "2399", "x-ratelimit-limit-requests-day": "2400",
                                    "x-ratelimit-remaining-tokens-minute": "29366", "x-ratelimit-limit-tokens-minute": "30000"})
    assert c["requests_day"]["remaining"] == 2399 and c["tokens_minute"]["limit"] == 30000


def test_429_cools_the_provider_down_and_the_next_one_answers(db, monkeypatch):
    def handler(url, body):
        if "groq" in url:
            return httpx.Response(429, headers={"retry-after": "120"}, text="rate limit")
        return answer([{"i": 0}])

    calls = fake_http(monkeypatch, handler)
    got = llm.complete_json("s", "u", {"type": "object"})
    assert got == {"results": [{"i": 0}]} and [("groq" in c["url"]) for c in calls] == [True, False]
    ok, why = P.ready("groq")
    assert not ok and why.startswith("cooldown")
    P._mem_at["t"] = 0  # another process reads the shared state from the DB
    assert P.ready("groq")[0] is False and llm.chain() == ["cerebras"]
    row = db.get(LlmProviderState, "groq")
    assert row.cooldown_until > datetime.utcnow() + timedelta(seconds=100)


def test_provider_comes_back_by_itself_after_the_reset(db, monkeypatch):
    P._save_state("groq", cooldown_until=datetime.utcnow() - timedelta(seconds=1), limits={})
    P._mem_at["t"] = 0
    assert P.ready("groq")[0] is True


def test_exhausted_daily_requests_wait_for_the_reset(db, monkeypatch):
    hdr = {**GROQ_HDR, "x-ratelimit-remaining-requests": "0", "x-ratelimit-reset-requests": "2h"}
    fake_http(monkeypatch, lambda url, body: answer([{"i": 0}], headers=hdr))
    assert P.chat("groq", "s", "u", {"type": "object"}, 100)[0] == "ok"
    ok, why = P.ready("groq")
    assert not ok and "cooldown" in why


def test_every_call_is_in_the_ledger_with_its_tokens(db, monkeypatch):
    fake_http(monkeypatch, lambda url, body: answer([{"i": 0}], usage={"total_tokens": 1234}))
    llm.complete_json("s", "u", {"type": "object"})
    assert P.usage_today("groq") == {"requests": 1, "tokens": 1234, "neurons": 0.0, "errors": 0}


def test_gpt_oss_is_asked_with_a_strict_schema_and_low_reasoning(db, monkeypatch):
    calls = fake_http(monkeypatch, lambda url, body: answer([]))
    llm.complete_json("s", "u", naming_ai.SCHEMA)
    b = calls[0]["body"]
    assert b["model"] == "openai/gpt-oss-20b" and b["response_format"]["json_schema"]["strict"] is True and b["reasoning_effort"] == "low"


def test_cloudflare_gets_the_schema_in_the_prompt_and_the_json_is_parsed_out(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "cf")
    monkeypatch.setenv("R2_ACCOUNT_ID", "acct")
    monkeypatch.setattr(P, "_ledger", lambda *a, **k: None)
    monkeypatch.setattr(P, "_save_state", lambda *a, **k: None)
    calls = fake_http(monkeypatch, lambda url, body: httpx.Response(200, json={"choices": [{"message": {"content": 'Sure: {"results": [{"i": 0}]}'}}],
                                                                                "usage": {"neurons": 7.8}}))
    status, got = P.chat("cloudflare", "s", "u", {"type": "object"}, 100)
    assert status == "ok" and got == {"results": [{"i": 0}]} and "JSON Schema" in calls[0]["body"]["messages"][0]["content"]
    assert "response_format" not in calls[0]["body"]


def test_batch_fits_the_live_token_headroom(db, monkeypatch):
    P._save_state("groq", limits={"tokens_minute": {"remaining": 7000}}, cooldown_until=None)
    assert naming_ai.batch_size(230) == 26          # (7000 − 900) // 230
    P._save_state("groq", limits={"tokens_minute": {"remaining": 30000}}, cooldown_until=None)
    assert naming_ai.batch_size(230) == 30          # capped at 30
    P._save_state("groq", limits={"tokens_minute": {"remaining": 2000}}, cooldown_until=None)
    assert naming_ai.batch_size(230) == 10          # floor of 10


# ---------------------------------------------------------------- judging an answer
def ad_(text, title=None, url=None):
    return Ad(ad_text=text, title=title, landing_url=url)


@pytest.mark.parametrize("r,status", [
    ({"product_name": "Posture Corrector", "product_type": "posture corrector", "confidence": 0.92, "evidence": "posture corrector", "is_physical_product": True}, "named"),
    ({"product_name": "Shop Now", "product_type": None, "confidence": 0.9, "evidence": "shop now", "is_physical_product": True}, "none"),
    ({"product_name": "Magnetic Necklace", "product_type": "necklace", "confidence": 0.95, "evidence": "magnetic necklace", "is_physical_product": True}, "review"),
    ({"product_name": "Posture Corrector", "product_type": "posture corrector", "confidence": 0.4, "evidence": "posture corrector", "is_physical_product": True}, "review"),
    ({"product_name": None, "product_type": None, "confidence": 0, "evidence": "", "is_physical_product": True}, "none"),
    ({"product_name": "Yoga Class", "product_type": "class", "confidence": 0.9, "evidence": "yoga", "is_physical_product": False}, "none"),
])
def test_judge_accepts_only_grounded_confident_product_names(r, status):
    ads = [ad_("Sinubukan ko itong posture corrector, ang ganda! Order na", "Best posture corrector PH")]
    assert naming_ai.judge(r, ads, {})["status"] == status   # 'Magnetic Necklace' is not in this ad: never accepted


def test_tagalog_and_arabic_answers_grounded_by_their_evidence_words():
    ar = [ad_("مصحح القوام الأفضل لظهرك، اطلب الآن")]
    r = {"product_name": "Posture Corrector", "product_type": "posture corrector", "confidence": 0.9, "evidence": "مصحح القوام", "is_physical_product": True}
    assert naming_ai.judge(r, ar, {})["status"] == "named"


# ---------------------------------------------------------------- the queue
def test_rules_name_from_evidence_without_any_model_call(db, monkeypatch):
    monkeypatch.setattr(llm, "complete_json", lambda *a, **k: pytest.fail("rules pass must not call a model"))
    p = product(db, [{"title": "Shop now", "ad_text": "Free shipping", "landing_url": "https://shop.example/products/magnetic-posture-corrector"}], checked=False)
    ad_id = db.scalar(select(Ad.id).where(Ad.product_id == p.id))
    out = naming_ai.rule_pass(db)
    db.flush()
    q = db.get(Product, db.scalar(select(Ad.product_id).where(Ad.id == ad_id)))
    assert out["named"] >= 1 and q.name_status in ("AUTO_CONFIRMED", "SUGGESTED") and "posture" in q.canonical_name.lower()


def test_ai_pass_names_reviews_and_skips_with_checkpoints(db, monkeypatch):
    a = product(db, [{"ad_text": "Sinubukan ko itong posture corrector, ang ganda!"}])
    b = product(db, [{"ad_text": "Order now! Limited stock, free shipping nationwide"}])
    c = product(db, [{"ad_text": "Meet our new collection, link in bio"}])
    m = product(db, [{"ad_text": "posture corrector"}], status="VERIFIED", name="MKT Name", manual=True)
    seen = []

    def fake(system, user, schema, **k):
        seen.append(user)
        lines = user.splitlines()
        res = []
        for i, line in enumerate(lines):
            if "posture corrector" in line:
                res.append({"i": i, "product_name": "Posture Corrector", "product_type": "posture corrector", "confidence": 0.93, "evidence": "posture corrector", "is_physical_product": True, "category": "health"})
            elif "collection" in line:
                res.append({"i": i, "product_name": "Summer Dress", "product_type": "dress", "confidence": 0.5, "evidence": "collection", "is_physical_product": True, "category": "fashion"})
            else:
                res.append({"i": i, "product_name": None, "product_type": None, "confidence": 0, "evidence": "", "is_physical_product": True, "category": "other"})
        return {"results": res}

    monkeypatch.setattr(llm, "complete_json", fake)
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(naming_ai, "batch_size", lambda per_item_tokens: 30)
    ids, orig = {a.id, b.id, c.id, m.id}, naming_ai._pending_ai
    db.commit()
    monkeypatch.setattr(naming_ai, "_pending_ai", lambda db, limit: [x for x in orig(db, 100000) if x.id in ids][:limit])  # the real queue query, our rows only
    a_ad = db.scalar(select(Ad.id).where(Ad.product_id == a.id))
    out = naming_ai.ai_pass(db, batches=3)
    db.flush()
    for x in (b, c, m):
        db.refresh(x)
    named = db.get(Product, db.scalar(select(Ad.product_id).where(Ad.id == a_ad)))
    assert named.name_status in ("AUTO_CONFIRMED", "VERIFIED", "SUGGESTED") and "posture corrector" in named.canonical_name.lower() and out["named"] == 1
    assert b.name_status == "UNKNOWN" and b.name_source == "llm_none"                       # no product named: never a guess
    assert c.name_status == "UNKNOWN" and c.name_source == "needs_review" and c.name_confidence <= 0.4
    assert any(e["source"] == "llm_hint" and e["value"] == "Summer Dress" for e in c.name_evidence)
    assert (m.canonical_name, m.name_status) == ("MKT Name", "VERIFIED")                    # a MKT name is never sent / touched
    assert out["requests"] == 1 and all("MKT Name" not in u for u in seen)                  # done products are not asked again


def test_no_provider_answer_marks_nothing_done(db, monkeypatch):
    p = product(db, [{"ad_text": "posture corrector"}])
    monkeypatch.setattr(llm, "complete_json", lambda *a, **k: None)
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(naming_ai, "_pending_ai", lambda db, limit: [p])
    out = naming_ai.ai_pass(db, batches=1)
    assert out["failed"] == 1 and p.name_source is None and p.naming_attempts == 1 and p.name_status == "UNKNOWN"


def test_line_the_model_skipped_stays_queued(db, monkeypatch):
    p = product(db, [{"ad_text": "Chest Bag Cross Body Men Ladies Bags"}])
    monkeypatch.setattr(llm, "complete_json", lambda *a, **k: {"results": []})  # big batches: gpt-oss sometimes drops lines
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(naming_ai, "_pending_ai", lambda db, limit: [p])
    out = naming_ai.ai_pass(db, batches=1)
    assert out["unanswered"] == 1 and p.name_source is None and p.naming_attempts == 1 and p.name_status == "UNKNOWN"


def test_dashboard_numbers(db, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    product(db, [{"ad_text": "x"}])
    db.commit()
    monkeypatch.setenv("ADMIN_TOKEN", "naming-stats-test")  # every /api route needs a session or the service token since auth
    s = TestClient(app, headers={"X-Admin-Token": "naming-stats-test"}).get("/api/naming/stats").json()
    assert {"products", "named", "unknown", "needs_review", "errors", "queue_rules", "queue_ai", "avg_confidence", "providers"} <= set(s)
    assert set(s["providers"]) == {"groq", "cerebras"} and s["providers"]["groq"]["model"] == "openai/gpt-oss-20b"


def test_tiktok_shop_deeplink_is_a_product_ad_not_an_app():
    from app.services import naming as N

    shop = "https://snssdk1180.onelink.me/BAuo?af_dp=snssdk1180%3A%2F%2Fec%2Fmall%3Fprevious_page%3Ddeeplink"
    assert N.ad_type({"title": "Chest Bag Cross Body Men Ladies Bags Oxford cloth", "ad_text": "Don't miss out!", "landing_url": shop, "funnel": "app"}) != "SERVICE_AD"
    assert N.ad_type({"title": "Play now", "ad_text": "Play now", "landing_url": "https://x.onelink.me/a?pid=1"}) == "SERVICE_AD"
