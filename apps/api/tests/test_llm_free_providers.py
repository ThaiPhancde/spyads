"""Free LLM provider chains (services/llm.py + llm_providers.py): strict schemas, per-model entries, cooldown rules, the vision /
transcribe chains, ledger budgets that survive a restart, and the rule fallbacks when nothing answers. Providers are a fake transport."""
import json
from datetime import datetime, timedelta

import httpx
import pytest

from app import creative_enrich, naming_ai
from app.db import SessionLocal
from app.models import Ad, LlmProviderState
from app.services import agent, llm, llm_providers as P, query_intent
from app.visual.models import ExternalApiCall


@pytest.fixture
def clean(monkeypatch):
    for k in ("GROQ_API_KEY", "CEREBRAS_API_KEY", "CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID", "R2_ACCOUNT_ID", "GEMINI_API_KEY",
              "GROQ_MODEL", "CEREBRAS_MODEL", "LLM_PROVIDERS", "VISION_PROVIDERS", "TRANSCRIBE_PROVIDERS"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(llm, "_disabled", False)
    with SessionLocal() as s:
        s.query(LlmProviderState).delete()
        s.query(ExternalApiCall).filter(ExternalApiCall.provider.like("llm_%")).delete(synchronize_session=False)
        s.commit()
    P._mem.clear()
    P._mem_at["t"] = 0
    llm.last_provider["name"] = None


@pytest.fixture
def keys(clean, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    monkeypatch.setenv("CEREBRAS_API_KEY", "csk_test")


def fake_http(monkeypatch, handler):
    calls = []

    def post(url, headers=None, json=None, timeout=None, data=None, files=None):
        calls.append({"url": url, "body": json, "data": data, "files": files, "auth": (headers or {}).get("Authorization")})
        return handler(url, json)

    monkeypatch.setattr(P.httpx, "post", post)
    return calls


def ok(content, headers=None):
    return httpx.Response(200, headers=headers or {}, json={"choices": [{"message": {"content": content}}], "usage": {"total_tokens": 100}})


# ---------------------------------------------------------------- schemas + chain shape
def closed(s) -> bool:
    if not isinstance(s, dict):
        return True
    if isinstance(s.get("properties"), dict):
        if s.get("additionalProperties") is not False or set(s["required"]) != set(s["properties"]):
            return False
    return all(closed(v) for v in (s.get("properties") or {}).values()) and closed(s.get("items"))


@pytest.mark.parametrize("schema", [query_intent.SCHEMA, agent.SPEC_SCHEMA, naming_ai.SCHEMA])
def test_every_schema_is_sent_in_strict_shape(schema):
    """Groq answered HTTP 400 'additionalProperties:false must be set on every object' to the query-intent schema (2026-11-03)."""
    assert closed(P.strict(schema))


def test_an_optional_field_becomes_nullable_not_dropped():
    s = P.strict({"type": "object", "properties": {"a": {"type": "string"}, "b": {"type": "string", "enum": ["x"]}}, "required": ["a"]})
    assert s["required"] == ["a", "b"] and s["properties"]["a"] == {"type": "string"} and s["properties"]["b"] == {"type": ["string", "null"], "enum": ["x", None]}


def test_default_chains_and_per_model_entries(keys):
    assert P.providers() == ["cerebras", "cerebras:qwen-3.8-27b", "groq:openai/gpt-oss-120b", "groq:openai/gpt-oss-20b", "cloudflare", "gemini"]
    assert P.config("groq:openai/gpt-oss-20b")["model"] == "openai/gpt-oss-20b" and P.config("groq:qwen/qwen3.8-27b")["reasoning"] is False
    assert llm.chain() == ["cerebras", "cerebras:qwen-3.8-27b", "groq:openai/gpt-oss-120b", "groq:openai/gpt-oss-20b"]  # no Cloudflare / Gemini key here
    assert llm.chain(env="TRANSCRIBE_PROVIDERS") == ["groq:whisper-large-v3-turbo"]


def test_two_entries_for_the_same_model_count_once(keys, monkeypatch):
    monkeypatch.setenv("GROQ_MODEL", "openai/gpt-oss-20b")
    monkeypatch.setenv("LLM_PROVIDERS", "groq,groq:openai/gpt-oss-20b,claude")
    assert P.providers() == ["groq", "claude"] and llm.chain() == ["groq"]  # an old 'claude' entry is simply never ready


# ---------------------------------------------------------------- failures + cooldowns
def test_off_schema_answer_is_not_a_provider_outage(keys, monkeypatch):
    """One answer off the schema (Groq json_validate_failed) used to cool Groq down for a minute for every feature."""
    def handler(url, body):
        if "cerebras" in url:
            return httpx.Response(400, json={"error": {"code": "json_validate_failed", "message": "Generated JSON does not match"}})
        return ok(json.dumps({"results": []}))
    calls = fake_http(monkeypatch, handler)
    assert llm.complete_json("s", "u", naming_ai.SCHEMA, kind="naming") == {"results": []}
    # both Cerebras entries (gpt-oss, qwen) are tried before the chain moves on to Groq
    assert [c["url"].split("/")[2] for c in calls] == ["api.cerebras.ai", "api.cerebras.ai", "api.groq.com"] and llm.last_provider["name"] == "groq:openai/gpt-oss-120b"
    P._mem_at["t"] = 0
    assert P.ready("cerebras")[0] is True
    with SessionLocal() as s:
        assert s.query(ExternalApiCall).filter_by(provider="llm_cerebras", error_code="json_validate_failed", kind="naming").count() == 1


def test_bad_key_cools_the_entry_down_for_30_minutes(keys, monkeypatch):
    fake_http(monkeypatch, lambda url, body: httpx.Response(401, json={"error": "invalid api key"}))
    assert P.chat("cerebras", "s", "u", None, 10) == ("error", None)
    cd = P._mem["cerebras"]["cooldown_until"]
    assert timedelta(minutes=29) < cd - datetime.utcnow() <= timedelta(minutes=30) and P.ready("cerebras")[0] is False


def test_an_old_zero_left_snapshot_does_not_park_a_provider_forever(keys):
    """Cerebras stayed unused from 2026-10-14: a 'requests_minute: 0 left' seen once was never refreshed (no call → no new headers)."""
    P._save_state("cerebras", limits={"requests_minute": {"remaining": 0, "reset_s": 30}}, cooldown_until=None)
    assert P.ready("cerebras") == (False, "requests_minute_exhausted")  # fresh: believe it
    P._mem["cerebras"]["updated_at"] = datetime.utcnow() - timedelta(minutes=2)
    assert P.ready("cerebras") == (True, None)  # older than its own reset: history


def test_no_key_anywhere_means_rules(clean):
    assert llm.available() is False and llm.engine() == "rules" and llm.complete_json("s", "u", {"type": "object"}) is None
    spec, engine = agent.parse("sản phẩm hot ở PH tuần này")
    assert engine == "rules" and spec["intent"]
    assert llm.read_image("x", b"jpg", "image/jpeg") is None and llm.transcribe("x", b"mp4", "video/mp4") is None


def test_network_error_falls_through_to_the_next_entry(keys, monkeypatch):
    def handler(url, body):
        if "cerebras" in url:
            raise httpx.ConnectTimeout("boom")
        return ok("Câu trả lời")
    fake_http(monkeypatch, handler)
    assert llm.complete_text("s", "u", kind="agent") == "Câu trả lời"
    P._mem_at["t"] = 0
    assert P.ready("cerebras")[0] is False  # 30 s network cooldown


# ---------------------------------------------------------------- agent: question → query spec → database rows
def test_agent_turns_a_question_into_a_spec_and_answers_from_the_database(keys, monkeypatch):
    spec = {k: None for k in agent.SPEC_SCHEMA["properties"]}
    spec.update(intent="product_search", limit=5)
    calls = fake_http(monkeypatch, lambda url, body: ok(json.dumps(spec) if body.get("response_format") else "Tóm tắt từ dữ liệu."))
    with SessionLocal() as db:
        out = agent.ask(db, "top sản phẩm đang chạy")
    assert out["engine"] == "cerebras" and out["spec"]["intent"] == "product_search" and "rows" in out
    assert calls[0]["body"]["response_format"]["json_schema"]["strict"] is True


# ---------------------------------------------------------------- ledger budgets survive a restart
def test_budgets_read_the_call_ledger(keys, monkeypatch):
    fake_http(monkeypatch, lambda url, body: ok(json.dumps({"is_product": False, "product": "", "variants": [], "related": [], "search_terms": {}})))
    monkeypatch.setattr(query_intent, "DAILY_CALLS", 1)
    assert query_intent._budget() is True
    assert llm.complete_json(query_intent.SYSTEM, "Keyword: x", query_intent.SCHEMA, kind="query_intent") is not None
    P._mem.clear()  # a "restart": nothing in memory, the ledger still knows
    assert llm.spent_today("query_intent") == 1 and llm.spent_today("naming") == 0 and query_intent._budget() is False


def test_gemini_has_one_daily_cap_for_every_feature(clean, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    monkeypatch.setenv("GEMINI_DAILY", "1")
    assert llm._gemini() is True
    P._ledger("gemini", True, 200, 5, None, kind="ocr")
    assert llm._gemini() is False and llm.available() is False


# ---------------------------------------------------------------- vision + transcribe chains
def test_ocr_sends_the_image_to_the_free_vision_model_with_thinking_off(keys, monkeypatch):
    calls = fake_http(monkeypatch, lambda url, body: ok("BUY 1 TAKE 1 bracelet"))
    assert llm.read_image("read it", b"\xff\xd8jpg", "image/jpeg", kind="ocr") == "BUY 1 TAKE 1 bracelet"
    b = calls[0]["body"]
    assert b["model"] == "qwen/qwen3.8-27b" and b["reasoning_effort"] == "none"
    assert b["messages"][1]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_ocr_order_free_vision_then_tesseract_then_gemini(keys, monkeypatch):
    seen = []
    monkeypatch.setattr(llm, "read_image", lambda *a, skip=(), **k: seen.append(("model", skip)) or None)
    monkeypatch.setattr(creative_enrich, "tesseract_langs", lambda: seen.append("tesseract") or None)
    assert creative_enrich.ocr_image(b"jpg", Ad(country="PH")) == (None, "none")
    assert seen == [("model", ("gemini",)), "tesseract", ("model", ())]


def test_whisper_gets_the_clip_as_is_and_gemini_is_the_fallback(keys, monkeypatch):
    calls = fake_http(monkeypatch, lambda url, body: httpx.Response(200, json={"text": " Order now, free shipping. "}))
    assert llm.transcribe("speech", b"mp4bytes", "video/mp4") == "Order now, free shipping."
    c = calls[0]
    assert c["url"].endswith("/audio/transcriptions") and c["data"]["model"] == "whisper-large-v3-turbo" and c["files"]["file"][1] == b"mp4bytes"
    with SessionLocal() as s:
        assert s.query(ExternalApiCall).filter_by(provider="llm_groq:whisper-large-v3-turbo", kind="transcript", ok=True).count() == 1
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    fake_http(monkeypatch, lambda url, body: httpx.Response(429, headers={"retry-after": "60"}, json={}))
    monkeypatch.setattr(llm, "_gemini_media", lambda *a: "Gemini heard it")
    assert llm.transcribe("speech", b"mp4bytes", "video/mp4") == "Gemini heard it" and llm.last_provider["name"] == "gemini"


# ---------------------------------------------------------------- the naming queue keeps its place
def test_providers_all_down_do_not_burn_the_products_attempts(monkeypatch):
    p = naming_ai.Product(product_code="X", canonical_name="?", name_status="UNKNOWN", naming_attempts=0)
    monkeypatch.setattr(naming_ai, "_pending_ai", lambda db, limit: [p])
    monkeypatch.setattr(naming_ai, "batch_size", lambda per_item_tokens: 10)
    monkeypatch.setattr(naming_ai, "_context", lambda *a: "x")
    state = {"up": True}
    monkeypatch.setattr(llm, "available", lambda env="LLM_PROVIDERS": state["up"])
    def down(*a, **k):
        state["up"] = False  # the request found every provider rate limited
    monkeypatch.setattr(llm, "complete_json", down)
    with SessionLocal() as db:
        out = naming_ai.ai_pass(db, batches=1)
        db.rollback()
    assert out["failed"] == 1 and p.naming_attempts == 0


def test_agent_schema_lets_the_model_say_not_mentioned():
    """Groq gpt-oss-120b answered null for 'not mentioned' (as the prompt asks) and was rejected: fast_growing / hidden_winner / limit were
    plain booleans / integer. 12 of 13 agent questions fell back to the regex parser (2026-11-03)."""
    props = P.strict(agent.SPEC_SCHEMA)["properties"]
    for k in ("fast_growing", "hidden_winner", "limit", "country", "days"):
        assert "null" in props[k]["type"], k
    assert agent.SPEC_SCHEMA["properties"]["intent"]["type"] == "string"  # the intent is never optional


def test_qwen_is_asked_not_to_think_with_each_providers_own_parameter(keys, monkeypatch):
    """qwen-3.8-27b spent all 900 max_tokens on reasoning and returned an empty answer: Cerebras naming 0/60, keyword meaning 1/6."""
    calls = fake_http(monkeypatch, lambda url, body: ok(json.dumps({"results": []})))
    P.chat("groq:qwen/qwen3.8-27b", "s", "u", {"type": "object"}, 100)
    P.chat("cerebras:qwen-3.8-27b", "s", "u", {"type": "object"}, 100)
    P.chat("cerebras", "s", "u", {"type": "object"}, 100)
    g, c, gpt = (x["body"] for x in calls)
    assert g["reasoning_effort"] == "none" and "disable_reasoning" not in g
    assert c["disable_reasoning"] is True and "reasoning_effort" not in c
    assert gpt["reasoning_effort"] == "low" and "disable_reasoning" not in gpt
