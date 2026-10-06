"""Optional Claude integration.

AI is used only to turn unstructured data into structured data and to *explain*
scores — never to decide a score (blueprint §20). Every caller has a rule-based
fallback, so the app works without ANTHROPIC_API_KEY.
"""
import json
import logging
import os

log = logging.getLogger(__name__)

MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-5-5")

_client = None
_disabled = os.getenv("DISABLE_LLM", "").lower() in ("1", "true", "yes")


def available() -> bool:
    if _disabled:
        return False
    return bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"))


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
            model=MODEL,
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
    resp = _create(
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
        output_config={"effort": effort},
    )
    if resp is None or resp.stop_reason == "refusal":
        return None
    return "".join(b.text for b in resp.content if b.type == "text") or None
