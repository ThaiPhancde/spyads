"""Free LLM providers behind one call: Cerebras → GroqCloud (two models) → Cloudflare Workers AI (→ Gemini in llm.py).

All three speak the OpenAI chat-completions dialect. A provider entry is "<provider>" (its *_MODEL env) or "<provider>:<model>": Groq and
Cerebras count their free limits per model, so a second model is a second free quota. Free limits (official docs, checked 2026-11-03):
  cerebras    gpt-oss-120b            5 req/min · 30k tok/min · 1M tok/day        x-ratelimit-remaining-{requests,tokens}-{minute,hour,day}
  cerebras    qwen-3.8-27b            the same limits again, counted per model (text + images); thinking switched off, see config()
  groq        openai/gpt-oss-120b     30 req/min · 1,000 req/day · 8k tok/min · 200k tok/day — same again for openai/gpt-oss-20b
  cloudflare  @cf/openai/gpt-oss-20b  no headers; usage.neurons per call (free 10,000 neurons/day, reset 00:00 UTC) — counted in our ledger
Media: qwen-3.8-27b (Cerebras + Groq) reads images (OCR), Groq whisper-large-v3-turbo transcribes audio / video (2,000 req/day, 25 MB).
A 429 or an exhausted counter puts the provider in cooldown until its reset (retry-after / reset header / next minute / next UTC day); the
caller moves on to the next provider at once and the cooled one is used again by itself after the reset. A 400 is our request's fault
(e.g. one answer off the schema), never the provider's health: no cooldown. State (remaining counters, cooldown, last error) lives in
`llm_provider_state` so the API and the worker share it and the dashboard can show it; every call is a row in `external_api_calls`
(provider "llm_<entry>", kind = the feature that asked, tokens / neurons) — the real per-day usage, and what daily budgets count.
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


OPENAI = ("groq", "cerebras", "cloudflare")
DEFAULTS = {"LLM_PROVIDERS": "cerebras,cerebras:qwen-3.8-27b,groq:openai/gpt-oss-120b,groq:openai/gpt-oss-20b,cloudflare,gemini",
            "VISION_PROVIDERS": "groq:qwen/qwen3.8-27b,cerebras:qwen-3.8-27b,gemini",
            "TRANSCRIBE_PROVIDERS": "groq:whisper-large-v3-turbo,gemini"}


def base(name: str) -> str:
    return name.split(":", 1)[0]


def is_openai(name: str) -> bool:
    return base(name) in OPENAI


def providers(env: str = "LLM_PROVIDERS") -> list[str]:
    """The entries of one chain (text / vision / transcribe), in order; two entries naming the same model count once."""
    out, seen = [], set()
    for p in (x.strip() for x in _env(env, DEFAULTS[env]).split(",")):
        key = (base(p), (config(p) or {}).get("model") if is_openai(p) else p)
        if p and key not in seen:
            seen.add(key)
            out.append(p)
    return out


def config(name: str) -> dict | None:
    """URL, key, model of an OpenAI-compatible provider entry, or None when it is not configured."""
    b, _, model = name.partition(":")
    if b == "groq" and _env("GROQ_API_KEY"):
        cfg = {"url": "https://api.groq.com/openai/v1/chat/completions", "key": _env("GROQ_API_KEY"), "model": _env("GROQ_MODEL", "openai/gpt-oss-120b"),
               "schema_mode": "json_schema"}
    elif b == "cerebras" and _env("CEREBRAS_API_KEY"):
        cfg = {"url": "https://api.cerebras.ai/v1/chat/completions", "key": _env("CEREBRAS_API_KEY"), "model": _env("CEREBRAS_MODEL", "gpt-oss-120b"),
               "schema_mode": "json_schema"}
    elif b == "cloudflare" and (_env("CLOUDFLARE_ACCOUNT_ID") or _env("R2_ACCOUNT_ID")) and _env("CLOUDFLARE_API_TOKEN"):
        # measured: Workers AI ignores response_format → the schema goes in the prompt and the JSON is parsed out of the answer
        acct = _env("CLOUDFLARE_ACCOUNT_ID") or _env("R2_ACCOUNT_ID")  # the R2 account id is the same Cloudflare account
        cfg = {"url": f"https://api.cloudflare.com/client/v4/accounts/{acct}/ai/v1/chat/completions", "key": _env("CLOUDFLARE_API_TOKEN"),
               "model": _env("CF_AI_MODEL", "@cf/openai/gpt-oss-20b"), "schema_mode": "prompt"}
    else:
        return None
    cfg["model"] = model or cfg["model"]
    cfg["reasoning"] = b != "cloudflare" and "gpt-oss" in cfg["model"]  # reasoning_effort is a gpt-oss parameter (whisper rejects it)
    # qwen thinks by default and spends the whole max_tokens on it (finish_reason "length", empty answer — measured 2026-11-03, 900 of 900
    # tokens); each provider switches it off with its own parameter
    cfg["extra"] = ({"reasoning_effort": "none"} if b == "groq" else {"disable_reasoning": True} if b == "cerebras" else {}) if "qwen" in cfg["model"] else {}
    return cfg


# ------------------------------------------------------------------ shared state (DB) + ledger
def _state_rows() -> dict[str, dict]:
    if time.time() - _mem_at["t"] < 5:
        return _mem
    try:
        from ..db import SessionLocal, read_only
        from ..models import LlmProviderState

        with read_only(), SessionLocal() as db:  # a plain read: never waits on a caller's SQLite write lock
            rows = {r.provider: {"cooldown_until": r.cooldown_until, "limits": r.limits or {}, "last_error": r.last_error, "updated_at": r.updated_at}
                    for r in db.query(LlmProviderState).all()}
        _mem.clear()
        _mem.update(rows)
        _mem_at["t"] = time.time()
    except Exception:  # noqa: BLE001 — no DB (unit tests of other modules): in-memory only
        pass
    return _mem


def _save_state(name: str, **fields):
    now = datetime.utcnow()
    _mem.setdefault(name, {}).update(fields, updated_at=now)
    try:
        from ..db import SessionLocal
        from ..models import LlmProviderState

        with SessionLocal() as db:
            row = db.get(LlmProviderState, name) or LlmProviderState(provider=name)
            for k, v in fields.items():
                setattr(row, k, v)
            row.updated_at = now
            db.add(row)
            db.commit()
    except Exception as e:  # noqa: BLE001
        log.debug("llm state not saved: %s", e)


def _ledger(name: str, ok: bool, status: int | None, ms: int, code: str | None, tokens: int | None = None, units: float | None = None,
            kind: str = "chat"):
    try:
        from ..db import SessionLocal
        from ..visual.models import ExternalApiCall

        with SessionLocal() as db:
            db.add(ExternalApiCall(provider=f"llm_{name}"[:64], kind=kind[:16], counted=status is not None and status < 500 and status != 401, ok=ok,
                                   http_status=status, error_code=code, duration_ms=ms, tokens=tokens, units=units))
            db.commit()
    except Exception as e:  # noqa: BLE001
        log.debug("llm ledger not written: %s", e)


def usage_today(name: str) -> dict:
    """Requests / tokens / neurons this UTC day from the ledger."""
    from sqlalchemy import func, select

    from ..db import SessionLocal, read_only
    from ..visual.models import ExternalApiCall

    day = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    with read_only(), SessionLocal() as db:
        r = db.execute(select(func.count(ExternalApiCall.id), func.coalesce(func.sum(ExternalApiCall.tokens), 0), func.coalesce(func.sum(ExternalApiCall.units), 0.0),
                              func.count(ExternalApiCall.id).filter(ExternalApiCall.ok.is_(False)))
                       .where(ExternalApiCall.provider == f"llm_{name}", ExternalApiCall.created_at >= day)).one()
    return {"requests": int(r[0]), "tokens": int(r[1]), "neurons": round(float(r[2]), 1), "errors": int(r[3])}


def spent_today(*kinds: str) -> int:
    """Model calls these features made this UTC day, over every provider — daily budgets read this, so a restart re-grants nothing."""
    from sqlalchemy import func, select

    from ..db import SessionLocal, read_only
    from ..visual.models import ExternalApiCall

    day = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        with read_only(), SessionLocal() as db:
            return db.scalar(select(func.count(ExternalApiCall.id)).where(
                ExternalApiCall.provider.like("llm\\_%", escape="\\"), ExternalApiCall.kind.in_(kinds), ExternalApiCall.created_at >= day)) or 0
    except Exception as e:  # noqa: BLE001 — no ledger, no budget: callers treat it as spent
        log.warning("llm ledger unreadable: %s", e)
        return 1 << 30


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
    if base(name) == "groq":  # requests window = day, tokens window = minute
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
    if not is_openai(name):
        return False, "not_openai_compatible"
    if config(name) is None:
        return False, "not_configured"
    st = _state_rows().get(name) or {}
    cd = st.get("cooldown_until")
    if cd and cd > datetime.utcnow():
        return False, f"cooldown until {cd.isoformat(timespec='seconds')}Z"
    if base(name) == "cloudflare":
        cap = float(_env("CF_AI_DAILY_NEURONS", "9500"))  # free 10,000 neurons/day, keep a margin
        if usage_today(name)["neurons"] >= cap:
            return False, "daily_neurons_used"
    age = (datetime.utcnow() - st["updated_at"]).total_seconds() if st.get("updated_at") else 0
    for win, v in (st.get("limits") or {}).items():
        # a snapshot older than its own reset is history: a "0 left" seen once used to park Cerebras for good (no call → no fresh headers)
        if v.get("remaining") is None or age > (v.get("reset_s") or {"minute": 60, "hour": 3600}.get(win.rsplit("_", 1)[-1], 86400)):
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


def strict(schema: dict) -> dict:
    """The schema in the shape OpenAI-style strict mode demands: every object closed (additionalProperties false) and every property
    required — an optional one becomes nullable instead. Groq rejected the loose query-intent / agent schemas outright (HTTP 400)."""
    if not isinstance(schema, dict):
        return schema
    out = dict(schema)
    if isinstance(out.get("properties"), dict):
        req = set(out.get("required") or [])
        props = {}
        for k, v in out["properties"].items():
            v = strict(v)
            if k not in req and isinstance(v, dict) and "type" in v:
                t = v["type"]
                v = {**v, "type": (t if "null" in t else [*t, "null"]) if isinstance(t, list) else [t, "null"]}
                if "enum" in v and None not in v["enum"]:
                    v["enum"] = [*v["enum"], None]
            props[k] = v
        out.update(properties=props, required=list(props), additionalProperties=False)
    if isinstance(out.get("items"), dict):
        out["items"] = strict(out["items"])
    for key in ("anyOf", "oneOf"):
        if isinstance(out.get(key), list):
            out[key] = [strict(x) for x in out[key]]
    return out


def _failed(name: str, r: httpx.Response, ms: int, kind: str) -> str:
    """Book a non-2xx answer; → the status chat() returns. Cooldowns only for the provider's own trouble."""
    limits = parse_limits(name, r.headers)
    if r.status_code == 429:
        retry = _dur(r.headers.get("retry-after"))
        exhausted = [w for w, v in limits.items() if v.get("remaining") == 0]
        until = datetime.utcnow() + timedelta(seconds=retry + 1) if retry else max((_until_reset(w, limits[w].get("reset_s")) for w in exhausted),
                                                                                    default=datetime.utcnow() + timedelta(seconds=61))
        _save_state(name, cooldown_until=until, limits=limits or (_mem.get(name) or {}).get("limits"), last_error=f"429 {r.text[:120]}")
        _ledger(name, False, 429, ms, "rate_limited", kind=kind)
        log.warning("llm %s rate limited until %s", name, until.isoformat(timespec="seconds"))
        return "rate_limited"
    code = "json_validate_failed" if "json_validate_failed" in r.text[:400] else f"http_{r.status_code}"
    if r.status_code in (400, 413, 422):  # our request (one answer off the schema, a too-big batch): the next request is fine
        _save_state(name, last_error=f"HTTP {r.status_code}: {r.text[:150]}")
    else:  # bad key / unknown model: 30 min; an outage: a minute
        cd = timedelta(minutes=30) if r.status_code in (401, 403, 404) else timedelta(seconds=60)
        _save_state(name, cooldown_until=datetime.utcnow() + cd, last_error=f"HTTP {r.status_code}: {r.text[:150]}")
    _ledger(name, False, r.status_code, ms, code, kind=kind)
    return "bad_output" if code == "json_validate_failed" else "error"


def _post(name: str, url: str, kind: str, timeout: float, **kw) -> tuple[httpx.Response | None, int]:
    t0 = time.time()
    try:
        r = httpx.post(url, headers={"Authorization": f"Bearer {config(name)['key']}"}, timeout=timeout, **kw)
    except httpx.HTTPError as e:
        ms = int((time.time() - t0) * 1000)
        _ledger(name, False, None, ms, "network_error" if not isinstance(e, httpx.TimeoutException) else "timeout", kind=kind)
        _save_state(name, cooldown_until=datetime.utcnow() + timedelta(seconds=30), last_error=f"{type(e).__name__}")
        return None, ms
    return r, int((time.time() - t0) * 1000)


def _ok(name: str, r: httpx.Response, ms: int, kind: str, tokens: int | None = None, units: float | None = None) -> None:
    limits = parse_limits(name, r.headers)
    exhausted = [w for w, v in limits.items() if v.get("remaining") is not None and v["remaining"] <= 0 and w.startswith("requests")]
    _save_state(name, limits=limits or (_mem.get(name) or {}).get("limits") or {}, last_error=None,
                cooldown_until=max((_until_reset(w, limits[w].get("reset_s")) for w in exhausted), default=None))
    _ledger(name, True, r.status_code, ms, None, tokens=tokens, units=units, kind=kind)


def chat(name: str, system: str, user: str, schema: dict | None, max_tokens: int, timeout: float = 60, kind: str = "chat",
         images: list[tuple[str, bytes]] | None = None) -> tuple[str, str | dict | None]:
    """One call to one provider entry; images = [(mime, bytes)] for a vision model. → ("ok", parsed json / text) |
    ("rate_limited"|"error"|"bad_output", None). Never raises."""
    import base64

    cfg = config(name)
    if cfg is None:
        return "error", None
    if schema is not None and cfg["schema_mode"] == "prompt":
        system = f"{system}\n\nRespond with ONLY one JSON object (no prose, no code fence) that matches this JSON Schema:\n{json.dumps(schema)}"
    content = user if not images else [{"type": "text", "text": user}, *({"type": "image_url", "image_url": {
        "url": f"data:{mime};base64,{base64.b64encode(data).decode()}"}} for mime, data in images)]
    body = {"model": cfg["model"], "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}], "max_tokens": max_tokens}
    if schema is not None and cfg["schema_mode"] == "json_schema":
        body["response_format"] = {"type": "json_schema", "json_schema": {"name": "result", "schema": strict(schema), "strict": True}}
    if cfg["reasoning"]:
        body["reasoning_effort"] = _env("LLM_REASONING_EFFORT", "low")  # gpt-oss: fewer hidden reasoning tokens on a free quota
    body.update(cfg["extra"])
    r, ms = _post(name, cfg["url"], kind, timeout, json=body)
    if r is None:
        return "error", None
    if r.status_code >= 400:
        return _failed(name, r, ms, kind), None
    try:
        j = r.json()
        text = j["choices"][0]["message"].get("content") or ""
        usage = j.get("usage") or {}
    except (ValueError, KeyError, IndexError):
        _ledger(name, False, r.status_code, ms, "bad_response", kind=kind)
        return "error", None
    _ok(name, r, ms, kind, tokens=usage.get("total_tokens"), units=usage.get("neurons"))
    if schema is None:
        return "ok", text.strip() or None
    data = _extract_json(text)
    if not isinstance(data, dict):
        return "bad_output", None
    return "ok", data


AUDIO_EXT = {"video/mp4": "mp4", "video/webm": "webm", "audio/mpeg": "mp3", "audio/mp4": "m4a", "audio/wav": "wav", "audio/x-wav": "wav",
             "audio/ogg": "ogg", "audio/webm": "webm"}


def transcribe(name: str, data: bytes, mime: str, timeout: float = 120, kind: str = "transcript") -> tuple[str, str | None]:
    """Speech → text through Groq's OpenAI-style /audio/transcriptions (Whisper takes mp4 / webm / mp3 / wav as they are, ≤ 25 MB free —
    nothing is re-encoded). → ("ok", text) | ("rate_limited"|"error", None)."""
    cfg = config(name)
    if cfg is None or base(name) != "groq":
        return "error", None
    r, ms = _post(name, cfg["url"].replace("/chat/completions", "/audio/transcriptions"), kind, timeout,
                  data={"model": cfg["model"], "response_format": "json"}, files={"file": (f"clip.{AUDIO_EXT.get(mime, 'mp4')}", data, mime)})
    if r is None:
        return "error", None
    if r.status_code >= 400:
        return _failed(name, r, ms, kind), None
    try:
        text = (r.json().get("text") or "").strip()
    except ValueError:
        _ledger(name, False, r.status_code, ms, "bad_response", kind=kind)
        return "error", None
    _ok(name, r, ms, kind)
    return "ok", text
