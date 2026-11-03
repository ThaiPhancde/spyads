"""LLM access for the whole app — free providers only, three chains (llm_providers.py has the providers and their limits):
  text        LLM_PROVIDERS         cerebras → groq gpt-oss-120b → groq gpt-oss-20b → cloudflare → gemini   complete_json / complete_text
  vision      VISION_PROVIDERS      groq qwen3.8-27b → cerebras qwen-3.8-27b → gemini                       read_image (OCR of a thumbnail)
  transcribe  TRANSCRIBE_PROVIDERS  groq whisper-large-v3-turbo → gemini                                    transcribe (speech in a clip)

AI is used only to turn unstructured data into structured data and to *explain* scores — never to decide a score (blueprint §20).
A provider that fails or is rate limited hands the request to the next one; every caller has a rule-based fallback, so the app works
with no provider at all. Nothing paid is ever called. Every call is a ledger row tagged with the feature that asked (kind): daily
budgets read spent_today(kind), so a restart re-grants nothing.
"""
import json
import logging
import os
import time

log = logging.getLogger(__name__)

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
_disabled = os.getenv("DISABLE_LLM", "").lower() in ("1", "true", "yes")
last_provider = {"name": None}  # which provider answered the last call (naming records it)


def _gemini() -> bool:
    """Key present and not in a rate-limit cooldown (shared by API + worker through llm_provider_state)."""
    from datetime import datetime

    from . import llm_providers as P

    if not os.getenv("GEMINI_API_KEY"):
        return False
    cd = (P._state_rows().get("gemini") or {}).get("cooldown_until")
    # its free quota is small and shared by every feature: GEMINI_DAILY calls a day in all (the ledger, so a restart re-grants nothing)
    return not (cd and cd > datetime.utcnow()) and P.usage_today("gemini")["requests"] < int(os.getenv("GEMINI_DAILY", "20"))


def _ready(name: str, need_tokens: int = 0) -> bool:
    from . import llm_providers as P

    return _gemini() if name == "gemini" else P.ready(name, need_tokens)[0]


def chain(need_tokens: int = 0, env: str = "LLM_PROVIDERS") -> list[str]:
    """Providers of one chain that can answer right now, in order."""
    from . import llm_providers as P

    return [] if _disabled else [n for n in P.providers(env) if _ready(n, need_tokens)]


def available(env: str = "LLM_PROVIDERS") -> bool:
    return bool(chain(env=env))


def engine() -> str:
    c = chain()
    return c[0] if c else "rules"


def model() -> str:
    from . import llm_providers as P

    e = engine()
    return GEMINI_MODEL if e == "gemini" else (P.config(e) or {}).get("model", e)


def spent_today(*kinds: str) -> int:
    from . import llm_providers as P

    return P.spent_today(*kinds)


def status() -> dict:
    """For /api/health-style endpoints: which engine answers right now and why not, if none."""
    from . import llm_providers as P

    reason = "DISABLE_LLM" if _disabled else None if available() else "every provider is rate limited / not configured"
    per = {n: ({"ready": _gemini(), "why": None if _gemini() else "no key / rate limited"} if n == "gemini" else dict(zip(("ready", "why"), P.ready(n))))
           for env in P.DEFAULTS for n in P.providers(env)}
    return {"engine": engine(), "available": available(), "providers": per, "reason": reason}


def _gemini_call(system: str, user: str, schema: dict | None, max_tokens: int, media: list[dict] | None = None,
                 retries: int = 3, timeout: float = 90, kind: str = "chat") -> str | None:
    import httpx

    from . import llm_providers as P

    cfg = {"maxOutputTokens": max_tokens}
    if schema:
        cfg.update(responseMimeType="application/json", responseJsonSchema=schema)
    t0, r = time.time(), None
    try:
        for attempt in range(max(1, retries)):  # free tier: 503/429 are transient (interactive callers pass retries=1)
            r = httpx.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent",
                headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"]},
                json={"systemInstruction": {"parts": [{"text": system}]},
                      "contents": [{"role": "user", "parts": [{"text": user}, *(media or [])]}],
                      "generationConfig": cfg},
                timeout=timeout,
            )
            if r.status_code not in (429, 503):
                break
            if attempt + 1 < retries:
                time.sleep(3 * (attempt + 1))
        ms = int((time.time() - t0) * 1000)
        if r.status_code in (429, 503):  # every ingest batch used to sit through these retries again: cool down for everyone
            from datetime import datetime, timedelta

            P._save_state("gemini", cooldown_until=datetime.utcnow() + timedelta(seconds=300), last_error=f"{r.status_code} {r.text[:120]}")
        r.raise_for_status()
        j = r.json()
        text = "".join(x.get("text", "") for x in j["candidates"][0]["content"]["parts"]) or None
        P._ledger("gemini", True, r.status_code, ms, None, tokens=(j.get("usageMetadata") or {}).get("totalTokenCount"), kind=kind)
        return text
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as e:  # free tier: 429 is common
        P._ledger("gemini", False, r.status_code if r is not None else None, int((time.time() - t0) * 1000), type(e).__name__, kind=kind)
        log.warning("Gemini error: %s", e)
        return None


def complete_json(system: str, user: str, schema: dict, max_tokens: int = 4000, retries: int = 3, timeout: float = 90,
                  kind: str = "chat") -> dict | None:
    """Structured extraction through the text chain. None when every provider is off, rate limited or failed."""
    from . import llm_providers as P

    for name in chain():
        if name == "gemini":
            text = _gemini_call(system, user, schema, max_tokens, retries=retries, timeout=timeout, kind=kind)
            try:
                got = json.loads(text) if text else None
            except json.JSONDecodeError:
                got = None
        else:
            status, got = P.chat(name, system, user, schema, max_tokens, timeout=timeout, kind=kind)
            got = got if status == "ok" else None
        if got is not None:
            last_provider["name"] = name
            return got
    return None


def complete_text(system: str, user: str, max_tokens: int = 4000, timeout: float = 60, kind: str = "chat") -> str | None:
    from . import llm_providers as P

    for name in chain():
        if name == "gemini":
            text = _gemini_call(system, user, None, max_tokens, timeout=timeout, kind=kind)
        else:
            status, text = P.chat(name, system, user, None, max_tokens, timeout=timeout, kind=kind)
            text = text if status == "ok" else None
        if text:
            last_provider["name"] = name
            return text
    return None


MEDIA_SYSTEM = "You extract text from ad media exactly as it appears. Never translate, summarise or add words."


def _gemini_media(instruction: str, data: bytes, mime: str, max_tokens: int, kind: str) -> str | None:
    import base64

    return _gemini_call(MEDIA_SYSTEM, instruction, None, max_tokens, media=[{"inlineData": {"mimeType": mime, "data": base64.b64encode(data).decode()}}],
                        kind=kind)


def read_image(instruction: str, data: bytes, mime: str, max_tokens: int = 1000, kind: str = "ocr", skip: tuple = ()) -> str | None:
    """Text visible in one small image (a ≤480 px thumbnail) through the vision chain (minus `skip`). None = no provider could answer."""
    from . import llm_providers as P

    for name in (n for n in chain(env="VISION_PROVIDERS") if n not in skip):
        if name == "gemini":
            out = _gemini_media(instruction, data, mime, max_tokens, kind)
        else:
            status, out = P.chat(name, MEDIA_SYSTEM, instruction, None, max_tokens, timeout=60, kind=kind, images=[(mime, data)])
            out = out if status == "ok" else None
        if out is not None:
            last_provider["name"] = name
            return out
    return None


def transcribe(instruction: str, data: bytes, mime: str, max_tokens: int = 1200, kind: str = "transcript") -> str | None:
    """Speech of a small clip (sent as it is, never re-encoded) through the transcribe chain: Whisper hears speech only; Gemini also
    reads burned-in subtitles (`instruction`). '' = nothing said. None = no provider could answer."""
    from . import llm_providers as P

    for name in chain(env="TRANSCRIBE_PROVIDERS"):
        if name == "gemini":
            out = _gemini_media(instruction, data, mime, max_tokens, kind)
        else:
            status, out = P.transcribe(name, data, mime, kind=kind)
            out = out if status == "ok" else None
        if out is not None:
            last_provider["name"] = name
            return out
    return None
