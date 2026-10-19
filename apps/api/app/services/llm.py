"""LLM access for the whole app — one call, a chain of providers (LLM_PROVIDERS, default groq,cerebras,cloudflare,gemini,claude).

AI is used only to turn unstructured data into structured data and to *explain* scores — never to decide a score (blueprint §20).
Free providers first (llm_providers.py reads their rate-limit headers and cools a provider down until its reset); a provider that fails or
is rate limited hands the request to the next one. Every caller has a rule-based fallback, so the app works with no provider at all.
Images / audio (media_text) stay on Gemini: the free OpenAI-compatible providers here are text-only.
"""
import json
import logging
import os

log = logging.getLogger(__name__)

CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-5-5")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")

_client = None
_disabled = os.getenv("DISABLE_LLM", "").lower() in ("1", "true", "yes")


_cool_until = 0.0  # Gemini free tier rate-limited us: skip it until then (Claude if a key exists, else the rule-based fallbacks)


def _gemini() -> bool:
    """Gemini is the engine right now: key present and not in a rate-limit cooldown."""
    import time

    return bool(os.getenv("GEMINI_API_KEY")) and time.time() >= _cool_until


def _claude() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"))


def chain(need_tokens: int = 0) -> list[str]:
    """Providers that can answer right now, in order."""
    from . import llm_providers as P

    out = []
    for name in P.providers():
        if name == "gemini":
            ok = _gemini()
        elif name == "claude":
            ok = _claude()
        else:
            ok = P.ready(name, need_tokens)[0]
        if ok:
            out.append(name)
    return out


def available() -> bool:
    return not _disabled and bool(chain())


def engine() -> str:
    c = chain()
    return c[0] if c else "rules"


def model() -> str:
    from . import llm_providers as P

    e = engine()
    return GEMINI_MODEL if e == "gemini" else CLAUDE_MODEL if e == "claude" else (P.config(e) or {}).get("model", e)


def status() -> dict:
    """For /api/health-style endpoints: which engine answers right now and why not, if none."""
    import time
    from datetime import datetime

    from . import llm_providers as P

    cooling = bool(os.getenv("GEMINI_API_KEY")) and time.time() < _cool_until
    reason = ("DISABLE_LLM" if _disabled else None if available() else "every provider is rate limited / not configured")
    per = {n: ({"ready": _gemini(), "why": "rate limited" if cooling else None} if n == "gemini" else {"ready": _claude()} if n == "claude"
               else dict(zip(("ready", "why"), P.ready(n)))) for n in P.providers()}
    return {"engine": engine() if available() else "rules", "available": available(), "providers": per,
            "cooldown_until": datetime.utcfromtimestamp(_cool_until).isoformat() if cooling else None, "reason": reason}


def _gemini_call(system: str, user: str, schema: dict | None, max_tokens: int, media: list[dict] | None = None,
                 retries: int = 3, timeout: float = 90) -> str | None:
    import httpx

    cfg = {"maxOutputTokens": max_tokens}
    if schema:
        cfg.update(responseMimeType="application/json", responseJsonSchema=schema)
    import time

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
        if r.status_code in (429, 503):
            # every ingest batch used to sit through these retries again (~18 s / 10 ads → 12-minute live searches)
            global _cool_until
            _cool_until = time.time() + 300
        r.raise_for_status()
        parts = r.json()["candidates"][0]["content"]["parts"]
        return "".join(x.get("text", "") for x in parts) or None
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as e:  # free tier: 429 is common
        log.warning("Gemini error: %s", e)
        return None


def media_text(instruction: str, data: bytes, mime: str, max_tokens: int = 1000) -> str | None:
    """One Gemini call over a small image / audio / video clip sent inline (OCR, transcript). Gemini only — Claude is not used
    here (cost). None when Gemini is off, cooling down or failed; the caller decides what that means."""
    import base64

    if _disabled or not _gemini():
        return None
    part = {"inlineData": {"mimeType": mime, "data": base64.b64encode(data).decode()}}
    return _gemini_call("You extract text from ad media exactly as it appears. Never translate, summarise or add words.",
                        instruction, None, max_tokens, media=[part])


def _get_client():
    global _client
    if _client is None:
        import anthropic

        _client = anthropic.Anthropic()
    return _client


def _create(**kwargs):
    import anthropic

    try:
        return _get_client().beta.messages.create(
            model=CLAUDE_MODEL,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            **kwargs,
        )
    except anthropic.RateLimitError as e:
        log.warning("Claude rate limited: %s", e)
    except anthropic.APIStatusError as e:
        log.warning("Claude API error %s: %s", e.status_code, e)
    except anthropic.APIConnectionError as e:
        log.warning("Claude connection error: %s", e)
    return None


def complete_json(system: str, user: str, schema: dict, effort: str = "low", max_tokens: int = 4000, retries: int = 3,
                  timeout: float = 90) -> dict | None:
    """Structured extraction through the provider chain. None when every provider is off, rate limited or failed."""
    if _disabled:
        return None
    from . import llm_providers as P

    for name in chain():
        if name == "gemini":
            text = _gemini_call(system, user, schema, max_tokens, retries=retries, timeout=timeout)
            try:
                got = json.loads(text) if text else None
            except json.JSONDecodeError:
                got = None
            if got is not None:
                return got
            continue
        if name == "claude":
            got = _claude_json(system, user, schema, effort, max_tokens)
            if got is not None:
                return got
            continue
        status, got = P.chat(name, system, user, schema, max_tokens, timeout=timeout)
        if status == "ok" and got is not None:
            last_provider["name"] = name
            return got
    return None


last_provider = {"name": None}  # which provider answered the last complete_json (naming records it)


def _claude_json(system: str, user: str, schema: dict, effort: str, max_tokens: int) -> dict | None:
    resp = _create(
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
        output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
    )
    if resp is None or resp.stop_reason == "refusal":
        return None
    text = next((b.text for b in resp.content if b.type == "text"), None)
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def complete_text(system: str, user: str, effort: str = "low", max_tokens: int = 4000) -> str | None:
    if _disabled:
        return None
    from . import llm_providers as P

    for name in chain():
        if name == "gemini":
            text = _gemini_call(system, user, None, max_tokens)
            if text:
                return text
            continue
        if name == "claude":
            break
        status, text = P.chat(name, system, user, None, max_tokens)
        if status == "ok" and text:
            return text
    if not _claude():
        return None
    resp = _create(
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
        output_config={"effort": effort},
    )
    if resp is None or resp.stop_reason == "refusal":
        return None
    return "".join(b.text for b in resp.content if b.type == "text") or None
