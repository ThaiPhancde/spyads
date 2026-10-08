"""Optional Claude integration.

AI is used only to turn unstructured data into structured data and to *explain*
scores — never to decide a score (blueprint §20). Every caller has a rule-based
fallback, so the app works without ANTHROPIC_API_KEY.
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


def available() -> bool:
    return not _disabled and (_gemini() or _claude())


def engine() -> str:
    return "gemini" if _gemini() else "claude"


def model() -> str:
    return GEMINI_MODEL if _gemini() else CLAUDE_MODEL


def status() -> dict:
    """For /api/health-style endpoints: which engine answers right now and why not, if none."""
    import time
    from datetime import datetime

    cooling = bool(os.getenv("GEMINI_API_KEY")) and time.time() < _cool_until
    reason = ("DISABLE_LLM" if _disabled else None if available() else
              "gemini rate-limited, no ANTHROPIC_API_KEY" if cooling else "no GEMINI_API_KEY / ANTHROPIC_API_KEY")
    return {"engine": engine() if available() else "rules", "available": available(),
            "cooldown_until": datetime.utcfromtimestamp(_cool_until).isoformat() if cooling else None, "reason": reason}


def _gemini_call(system: str, user: str, schema: dict | None, max_tokens: int) -> str | None:
    import httpx

    cfg = {"maxOutputTokens": max_tokens}
    if schema:
        cfg.update(responseMimeType="application/json", responseJsonSchema=schema)
    import time

    try:
        for attempt in range(3):  # free tier: 503/429 are transient
            r = httpx.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent",
            headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"]},
            json={"systemInstruction": {"parts": [{"text": system}]},
                  "contents": [{"role": "user", "parts": [{"text": user}]}],
                  "generationConfig": cfg},
            timeout=90,
            )
            if r.status_code not in (429, 503):
                break
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


def complete_json(system: str, user: str, schema: dict, effort: str = "low", max_tokens: int = 4000) -> dict | None:
    """Structured extraction. Returns None when LLM is unavailable or fails."""
    if not available():
        return None
    if _gemini():
        text = _gemini_call(system, user, schema, max_tokens)
        try:
            return json.loads(text) if text else None
        except json.JSONDecodeError:
            return None
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
    if not available():
        return None
    if _gemini():
        return _gemini_call(system, user, None, max_tokens)
    resp = _create(
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
        output_config={"effort": effort},
    )
    if resp is None or resp.stop_reason == "refusal":
        return None
    return "".join(b.text for b in resp.content if b.type == "text") or None
