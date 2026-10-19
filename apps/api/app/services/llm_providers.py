"""Free-first LLM providers behind one call: GroqCloud → Cerebras → Cloudflare Workers AI (→ Gemini → Claude in llm.py).

All three speak the OpenAI chat-completions dialect. Each provider's real limits are read from its own response headers (measured 2026-10-14):
  groq        openai/gpt-oss-20b   x-ratelimit-{limit,remaining}-{requests,tokens} (1,000 req/day · 8,000 tok/min), reset as "1m26.4s"
  cerebras    gpt-oss-120b         x-ratelimit-remaining-{requests,tokens}-{minute,hour,day} (5 req/min · 150/h · 2,400/day · 30k tok/min · 1M/day)
  cloudflare  @cf/openai/gpt-oss-20b  no headers; usage.neurons per call (free 10,000 neurons/day, reset 00:00 UTC) — counted in our ledger
A 429 or an exhausted counter puts the provider in cooldown until its reset (retry-after / reset header / next minute / next UTC day); the
caller moves on to the next provider at once and the cooled one is used again by itself after the reset. State (remaining counters,
cooldown, last error) lives in `llm_provider_state` so the API and the worker share it and the dashboard can show it; every call is a
row in `external_api_calls` (provider "llm_<name>", tokens / neurons) — the real per-day usage.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timedelta

import httpx

log = logging.getLogger("app.llm")
_lock = threading.Lock()
_mem: dict[str, dict] = {}  # provider → state snapshot (refreshed from the DB every few seconds)
_mem_at = {"t": 0.0}


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).split("#")[0].strip()


def providers() -> list[str]:
    return [p.strip() for p in _env("LLM_PROVIDERS", "groq,cerebras,cloudflare,gemini,claude").split(",") if p.strip()]


def config(name: str) -> dict | None:
    """URL, key, model of an OpenAI-compatible provider, or None when it is not configured."""
    if name == "groq" and _env("GROQ_API_KEY"):
        return {"url": "https://api.groq.com/openai/v1/chat/completions", "key": _env("GROQ_API_KEY"), "model": _env("GROQ_MODEL", "openai/gpt-oss-20b"),
                "schema_mode": "json_schema", "reasoning": True}
    if name == "cerebras" and _env("CEREBRAS_API_KEY"):
        return {"url": "https://api.cerebras.ai/v1/chat/completions", "key": _env("CEREBRAS_API_KEY"), "model": _env("CEREBRAS_MODEL", "gpt-oss-120b"),
                "schema_mode": "json_schema", "reasoning": True}
    acct = _env("CLOUDFLARE_ACCOUNT_ID") or _env("R2_ACCOUNT_ID")
    if name == "cloudflare" and acct and _env("CLOUDFLARE_API_TOKEN"):
        # measured: Workers AI ignores response_format → the schema goes in the prompt and the JSON is parsed out of the answer
        return {"url": f"https://api.cloudflare.com/client/v4/accounts/{acct}/ai/v1/chat/completions", "key": _env("CLOUDFLARE_API_TOKEN"),
                "model": _env("CF_AI_MODEL", "@cf/openai/gpt-oss-20b"), "schema_mode": "prompt", "reasoning": False}
    return None


# ------------------------------------------------------------------ shared state (DB) + ledger
def _state_rows() -> dict[str, dict]:
    if time.time() - _mem_at["t"] < 5:
        return _mem
    try:
        from ..db import SessionLocal
        from ..models import LlmProviderState

        with SessionLocal() as db:
            rows = {r.provider: {"cooldown_until": r.cooldown_until, "limits": r.limits or {}, "last_error": r.last_error, "updated_at": r.updated_at}
                    for r in db.query(LlmProviderState).all()}
        _mem.clear()
        _mem.update(rows)
        _mem_at["t"] = time.time()
    except Exception:  # noqa: BLE001 — no DB (unit tests of other modules): in-memory only
        pass
    return _mem


def _save_state(name: str, **fields):
    _mem.setdefault(name, {}).update(fields)
    try:
        from ..db import SessionLocal
        from ..models import LlmProviderState

        with SessionLocal() as db:
            row = db.get(LlmProviderState, name) or LlmProviderState(provider=name)
            for k, v in fields.items():
                setattr(row, k, v)
            row.updated_at = datetime.utcnow()
            db.add(row)
            db.commit()
    except Exception as e:  # noqa: BLE001
        log.debug("llm state not saved: %s", e)


def _ledger(name: str, ok: bool, status: int | None, ms: int, code: str | None, tokens: int | None = None, units: float | None = None):
    try:
        from ..db import SessionLocal
        from ..visual.models import ExternalApiCall

        with SessionLocal() as db:
            db.add(ExternalApiCall(provider=f"llm_{name}", kind="chat", counted=status is not None and status < 500 and status != 401, ok=ok,
                                   http_status=status, error_code=code, duration_ms=ms, tokens=tokens, units=units))
            db.commit()
    except Exception as e:  # noqa: BLE001
        log.debug("llm ledger not written: %s", e)


def usage_today(name: str) -> dict:
    """Requests / tokens / neurons this UTC day from the ledger."""
    from sqlalchemy import func, select

    from ..db import SessionLocal
    from ..visual.models import ExternalApiCall

    day = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    with SessionLocal() as db:
        r = db.execute(select(func.count(ExternalApiCall.id), func.coalesce(func.sum(ExternalApiCall.tokens), 0), func.coalesce(func.sum(ExternalApiCall.units), 0.0),
                              func.count(ExternalApiCall.id).filter(ExternalApiCall.ok.is_(False)))
                       .where(ExternalApiCall.provider == f"llm_{name}", ExternalApiCall.created_at >= day)).one()
    return {"requests": int(r[0]), "tokens": int(r[1]), "neurons": round(float(r[2]), 1), "errors": int(r[3])}


# ------------------------------------------------------------------ rate-limit headers
def _dur(s: str | None) -> float | None:
    """'1m26.4s' / '3.585s' / '45' / '120ms' → seconds."""
    if not s:
        return None
    s = s.strip()
    try:
        return float(s)
    except ValueError:
        pass
    total, ok = 0.0, False
    for num, unit in re.findall(r"([\d.]+)\s*(ms|h|m|s)", s):
        ok = True
        total += float(num) * {"ms": 0.001, "h": 3600, "m": 60, "s": 1}[unit]
    return total if ok else None


def parse_limits(name: str, headers) -> dict:
    """Normalise each provider's headers into {window: {limit, remaining, reset_s}} (windows: requests/tokens × minute/hour/day)."""
    h = {k.lower(): v for k, v in headers.items()}
    out: dict = {}
    if name == "groq":  # requests window = day, tokens window = minute
        for kind, win in (("requests", "day"), ("tokens", "minute")):
            lim, rem = h.get(f"x-ratelimit-limit-{kind}"), h.get(f"x-ratelimit-remaining-{kind}")
            if rem is not None:
                out[f"{kind}_{win}"] = {"limit": int(float(lim)) if lim else None, "remaining": int(float(rem)), "reset_s": _dur(h.get(f"x-ratelimit-reset-{kind}"))}
    else:  # cerebras style: x-ratelimit-remaining-<kind>-<window>
        for k, v in h.items():
            m = re.fullmatch(r"x-ratelimit-remaining-(requests|tokens)-(minute|hour|day)", k)
            if m:
                lim = h.get(f"x-ratelimit-limit-{m.group(1)}-{m.group(2)}")
                out[f"{m.group(1)}_{m.group(2)}"] = {"limit": int(float(lim)) if lim else None, "remaining": int(float(v)),
                                                     "reset_s": _dur(h.get(f"x-ratelimit-reset-{m.group(1)}-{m.group(2)}"))}
    return out


def _until_reset(window: str, reset_s: float | None) -> datetime:
    now = datetime.utcnow()
    if reset_s:
        return now + timedelta(seconds=reset_s + 1)
    if window.endswith("day"):
        return (now + timedelta(days=1)).replace(hour=0, minute=0, second=5, microsecond=0)
    return now + timedelta(hours=1) if window.endswith("hour") else now + timedelta(seconds=61)


def ready(name: str, need_tokens: int = 0) -> tuple[bool, str | None]:
    """Can this provider take a request of ~need_tokens now? (no network)"""
    if name not in ("groq", "cerebras", "cloudflare"):
        return False, "not_openai_compatible"
    if config(name) is None:
        return False, "not_configured"
    st = _state_rows().get(name) or {}
    cd = st.get("cooldown_until")
    if cd and cd > datetime.utcnow():
        return False, f"cooldown until {cd.isoformat(timespec='seconds')}Z"
    if name == "cloudflare":
        cap = float(_env("CF_AI_DAILY_NEURONS", "9500"))  # free 10,000 neurons/day, keep a margin
        if usage_today("cloudflare")["neurons"] >= cap:
            return False, "daily_neurons_used"
    for win, v in (st.get("limits") or {}).items():
        if v.get("remaining") is None:
            continue
        if win.startswith("requests") and v["remaining"] <= 0:
            return False, f"{win}_exhausted"
        if win.startswith("tokens") and need_tokens and v["remaining"] < need_tokens:
            return False, f"{win}_low"
    return True, None


def headroom(name: str) -> int | None:
    """Tokens this provider can take right now (smallest token window remaining), None when unknown."""
    lim = (_state_rows().get(name) or {}).get("limits") or {}
    vals = [v["remaining"] for w, v in lim.items() if w.startswith("tokens") and v.get("remaining") is not None]
    return min(vals) if vals else None


def _extract_json(text: str) -> dict | None:
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        pass
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except ValueError:
            return None
    return None


def chat(name: str, system: str, user: str, schema: dict | None, max_tokens: int, timeout: float = 60) -> tuple[str, str | dict | None]:
    """One call to one provider. → ("ok", parsed json / text) | ("rate_limited"|"error"|"bad_output", None). Never raises."""
    cfg = config(name)
    if cfg is None:
        return "error", None
    if schema is not None and cfg["schema_mode"] == "prompt":
        system = f"{system}\n\nRespond with ONLY one JSON object (no prose, no code fence) that matches this JSON Schema:\n{json.dumps(schema)}"
    body = {"model": cfg["model"], "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}], "max_tokens": max_tokens}
    if schema is not None and cfg["schema_mode"] == "json_schema":
        body["response_format"] = {"type": "json_schema", "json_schema": {"name": "result", "schema": schema, "strict": True}}
    if cfg["reasoning"]:
        body["reasoning_effort"] = _env("LLM_REASONING_EFFORT", "low")  # gpt-oss: fewer hidden reasoning tokens on a free quota
    t0 = time.time()
    try:
        r = httpx.post(cfg["url"], headers={"Authorization": f"Bearer {cfg['key']}"}, json=body, timeout=timeout)
    except httpx.HTTPError as e:
        ms = int((time.time() - t0) * 1000)
        _ledger(name, False, None, ms, "network_error" if not isinstance(e, httpx.TimeoutException) else "timeout")
        _save_state(name, cooldown_until=datetime.utcnow() + timedelta(seconds=30), last_error=f"{type(e).__name__}")
        return "error", None
    ms = int((time.time() - t0) * 1000)
    limits = parse_limits(name, r.headers)
    if r.status_code == 429:
        retry = _dur(r.headers.get("retry-after"))
        exhausted = [w for w, v in limits.items() if v.get("remaining") == 0]
        until = datetime.utcnow() + timedelta(seconds=retry + 1) if retry else max((_until_reset(w, limits[w].get("reset_s")) for w in exhausted),
                                                                                    default=datetime.utcnow() + timedelta(seconds=61))
        _save_state(name, cooldown_until=until, limits=limits or (_mem.get(name) or {}).get("limits"), last_error=f"429 {r.text[:120]}")
        _ledger(name, False, 429, ms, "rate_limited")
        log.warning("llm %s rate limited until %s", name, until.isoformat(timespec="seconds"))
        return "rate_limited", None
    if r.status_code >= 400:
        cd = timedelta(minutes=30) if r.status_code in (401, 403) else timedelta(seconds=60)
        _save_state(name, cooldown_until=datetime.utcnow() + cd, last_error=f"HTTP {r.status_code}: {r.text[:150]}")
        _ledger(name, False, r.status_code, ms, f"http_{r.status_code}")
        return "error", None
    try:
        j = r.json()
        content = j["choices"][0]["message"].get("content") or ""
        usage = j.get("usage") or {}
    except (ValueError, KeyError, IndexError):
        _ledger(name, False, r.status_code, ms, "bad_response")
        return "error", None
    exhausted = [w for w, v in limits.items() if v.get("remaining") is not None and v["remaining"] <= 0 and w.startswith("requests")]
    _save_state(name, limits=limits or (_mem.get(name) or {}).get("limits") or {}, last_error=None,
                cooldown_until=max((_until_reset(w, limits[w].get("reset_s")) for w in exhausted), default=None))
    _ledger(name, True, r.status_code, ms, None, tokens=usage.get("total_tokens"), units=usage.get("neurons"))
    if schema is None:
        return "ok", content.strip() or None
    data = _extract_json(content)
    if not isinstance(data, dict):
        return "bad_output", None
    return "ok", data
