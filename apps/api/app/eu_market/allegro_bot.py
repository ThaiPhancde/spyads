"""Allegro product search jobs — contract `spyads.agent.v1` (docs/grokbot_integration.md).

Three providers, one job table (EUBotJob), one result path (allegro_import.map_item / allegro_serp.normalize → service.upsert_items):
  serp  SpyAds calls Google Shopping via SerpApi itself (allegro_serp.py) in a background task. Cheap, no agent needed.
  grok  SpyAds POSTs a task to the Grok Bot (J.A.R.V.I.S) webhook; the agent calls our search tool
        (POST /api/agent/v1/tools/allegro_search, Bearer GROK_TOOL_TOKEN), picks / annotates results and POSTs the result
        envelope to /api/eu-market/allegro/callback/{job_id}?token=… with X-Callback-Secret.
  bridge  the job only waits: the user's own Chrome tab + extension (extensions/allegro-bridge) reads the Allegro listing
        page and POSTs /api/imports/allegro {records, job_id, status} (routers/bridge.py → finish_bridge). No server-side
        request to Allegro, no SerpApi, no webhook. Deadline BRIDGE_DEADLINE (the user may have to solve a CAPTCHA in their tab).
Shared: cache (a done job for the same folded keyword within ALLEGRO_BOT_CACHE_HOURS is returned), in-flight dedupe,
lazy timeout, webhook retry with backoff, CAPTCHA / blocked reported as such (never solved / worked around), no fake data.
Env (values never leave the server): GROK_ROUTINE_WEBHOOK_URL, GROK_ROUTINE_WEBHOOK_KEY, GROK_TOOL_TOKEN, ALLEGRO_CALLBACK_SECRET,
PUBLIC_API_BASE_URL (public HTTPS — the agent runs in the cloud), ALLEGRO_BOT_TIMEOUT_MIN (15), ALLEGRO_BOT_CACHE_HOURS (12).
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from datetime import datetime, timedelta
from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import SessionLocal
from .allegro_import import map_item
from .models import EUBotJob
from .service import fold, upsert_items

SOURCE, CONTRACT = "allegro", "spyads.agent.v1"
LIVE = ("queued", "running", "sent")
FINAL = ("done", "empty", "captcha", "blocked", "failed", "timeout")
BOT_STATUS = {"ok": "done", "done": "done", "success": "done", "empty": "empty", "no_results": "empty",
              "captcha": "captcha", "blocked": "blocked", "error": "failed", "failed": "failed"}
MAX_ITEMS = 100
SERP_DEADLINE = timedelta(minutes=3)
BRIDGE_DEADLINE = timedelta(minutes=3)
BRIDGE_STATUS = {"ok": "done", "blocked": "blocked", "captcha": "captcha", "error": "failed"}  # extension report → job status
MESSAGES = {
    "captcha": "Nguồn hiện CAPTCHA — agent dừng, không giải CAPTCHA. Thử lại sau.",
    "blocked": "Nguồn chặn truy cập (DataDome / access denied) — agent dừng, không vượt chặn.",
    "timeout": "Không có kết quả trước hạn — routine lỗi, hoặc callback URL không gọi được từ cloud.",
    "empty": "Tìm xong nhưng không có sản phẩm Allegro cho từ khoá này.",
}
BRIDGE_MESSAGES = {
    "queued": "Đang chờ extension Allegro Bridge trong tab Chrome của bạn.",
    "captcha": "Allegro hiện CAPTCHA trong tab của bạn — tự giải trong tab rồi chạy lại. ToolSpy không giải CAPTCHA.",
    "blocked": "Allegro chặn truy cập (DataDome) trong tab của bạn — ToolSpy không vượt chặn. Thử lại sau.",
    "timeout": "Extension không gửi kết quả trong 3 phút — tab đã đóng, chưa cài extension, hoặc sai token.",
}


class BotConfigError(Exception):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code, self.detail = code, detail


def _env_int(k: str, d: int) -> int:
    try:
        return int(os.getenv(k, d))
    except ValueError:
        return d


def config() -> dict:
    """Booleans only — secret values never leave the server."""
    from . import allegro_serp

    base = (os.getenv("PUBLIC_API_BASE_URL") or "").rstrip("/")
    host = (urlparse(base).hostname or "").lower()
    public = base.startswith("https://") and host not in ("localhost", "127.0.0.1", "0.0.0.0", "::1") and not host.endswith(".local")
    return {"serp_ready": allegro_serp.unavailable() is None, "webhook_url": bool(os.getenv("GROK_ROUTINE_WEBHOOK_URL")),
            "webhook_key": bool(os.getenv("GROK_ROUTINE_WEBHOOK_KEY")), "tool_token": bool(os.getenv("GROK_TOOL_TOKEN")),
            "callback_secret": bool(os.getenv("ALLEGRO_CALLBACK_SECRET")), "public_base_url": base or None, "public_base_ok": public,
            "bridge_token": bool(os.getenv("TOOLSPY_BRIDGE_TOKEN")),
            "timeout_min": _env_int("ALLEGRO_BOT_TIMEOUT_MIN", 15), "cache_hours": _env_int("ALLEGRO_BOT_CACHE_HOURS", 12)}


def _check_grok(c: dict):
    missing = [k for k, v in (("GROK_ROUTINE_WEBHOOK_URL", c["webhook_url"]), ("GROK_ROUTINE_WEBHOOK_KEY", c["webhook_key"]),
                              ("GROK_TOOL_TOKEN", c["tool_token"]), ("ALLEGRO_CALLBACK_SECRET", c["callback_secret"])) if not v]
    if missing:
        raise BotConfigError("missing_credentials", "Chưa đặt " + ", ".join(missing) + " trong .env của server.")
    if not c["public_base_ok"]:
        raise BotConfigError("callback_not_public", "PUBLIC_API_BASE_URL phải là HTTPS công khai (domain hoặc Cloudflare Tunnel); Grok Bot chạy trên cloud không gọi được localhost.")


def job_dict(j: EUBotJob) -> dict:
    return {"id": j.id, "source": j.source, "provider": j.provider or "grok", "keyword": j.keyword, "limit": j.limit,
            "status": j.status, "final": j.status in FINAL, "created_at": j.created_at, "sent_at": j.sent_at,
            "finished_at": j.finished_at, "deadline_at": j.deadline_at, "error_code": j.error_code, "error_detail": j.error_detail,
            "message": j.error_detail if j.status == "failed" else
            (j.provider == "bridge" and BRIDGE_MESSAGES.get(j.status)) or MESSAGES.get(j.status),
            "items_received": j.items_received, "items_written": j.items_written, "listing_ids": j.listing_ids or []}


def expire(db: Session, now: datetime | None = None) -> int:
    """Lazy timeout (on every read / request — no scheduler)."""
    now = now or datetime.utcnow()
    rows = db.scalars(select(EUBotJob).where(EUBotJob.status.in_(LIVE), EUBotJob.deadline_at < now)).all()
    for j in rows:
        j.status, j.finished_at, j.error_code = "timeout", now, "timeout"
    if rows:
        db.commit()
    return len(rows)


def _reuse(db: Session, key: str, limit: int, cache_hours: int, in_flight: bool, provider: str) -> dict | None:
    now = datetime.utcnow()
    if in_flight:  # same provider only: a serp job must run run_serp, a bridge job can be finished only by the extension
        live = db.scalar(select(EUBotJob).where(EUBotJob.source == SOURCE, EUBotJob.keyword_key == key, EUBotJob.status.in_(LIVE),
                                                EUBotJob.provider == provider)
                         .order_by(EUBotJob.created_at.desc()).limit(1))
        if live:
            return {**job_dict(live), "reused": "in_flight"}
    hit = db.scalar(select(EUBotJob).where(EUBotJob.source == SOURCE, EUBotJob.keyword_key == key, EUBotJob.status == "done",
                                           EUBotJob.limit >= limit, EUBotJob.finished_at >= now - timedelta(hours=cache_hours))
                    .order_by(EUBotJob.finished_at.desc()).limit(1))
    return {**job_dict(hit), "reused": "cache"} if hit else None


def request_search(db: Session, keyword: str, limit: int = 5, force: bool = False, provider: str = "serp", post=None,
                   reuse_in_flight: bool = True) -> dict:
    """Cache / in-flight reuse, else a new job. provider=serp → status "running" (caller runs run_serp); grok → webhook sent;
    bridge → "queued" until the extension reports (finish_bridge)."""
    keyword = " ".join((keyword or "").split())[:200]
    if not keyword:
        raise ValueError("keyword required")
    if provider not in ("serp", "grok", "bridge"):
        raise ValueError("provider must be serp | grok | bridge")
    limit = max(1, min(int(limit or 5), 50))
    key, now, c = fold(keyword)[:255], datetime.utcnow(), config()
    if provider == "grok":
        _check_grok(c)
    elif provider == "serp" and not c["serp_ready"]:
        raise BotConfigError("missing_api_key", "SERPAPI_API_KEY chưa đặt trong .env của server.")
    expire(db, now)
    if not force and (hit := _reuse(db, key, limit, c["cache_hours"], reuse_in_flight, provider)):
        return hit
    token = secrets.token_urlsafe(24)
    j = EUBotJob(id=secrets.token_hex(12), source=SOURCE, provider=provider, keyword=keyword, keyword_key=key, limit=limit,
                 status="running" if provider == "serp" else "queued", token_hash=hashlib.sha256(token.encode()).hexdigest(),
                 created_at=now, deadline_at=now + {"serp": SERP_DEADLINE, "bridge": BRIDGE_DEADLINE}.get(provider, timedelta(minutes=c["timeout_min"])),
                 listing_ids=[])
    db.add(j)
    db.commit()
    if provider == "grok":
        _dispatch(db, j, token, c, post or httpx.post)
    return {**job_dict(j), "reused": None}


def task_envelope(j: EUBotJob, token: str, base: str) -> dict:
    """SpyAds → Grok Bot task (contract spyads.agent.v1). Secrets are placeholders: the routine stores the real values."""
    return {
        "contract": CONTRACT, "type": "task", "job_id": j.id, "deadline_at": j.deadline_at.isoformat() + "Z",
        "task": {"kind": "product_search", "source": SOURCE, "market": "PL", "currency": "PLN", "keyword": j.keyword, "limit": j.limit},
        "tools": [{"name": "allegro_search", "method": "POST", "url": f"{base}/api/agent/v1/tools/allegro_search",
                   "headers": {"Authorization": "Bearer <GROK_TOOL_TOKEN>", "Content-Type": "application/json"},
                   "input": {"keyword": j.keyword, "limit": j.limit, "job_id": j.id},
                   "returns": "{contract, type: tool_result, status, items: [listing], cached, quota}"}],
        "callback": {"url": f"{base}/api/eu-market/allegro/callback/{j.id}?token={token}", "method": "POST",
                     "headers": {"X-Callback-Secret": "<ALLEGRO_CALLBACK_SECRET>", "Content-Type": "application/json"}},
        "rules": ["Use only the tools listed here; do not open allegro.pl directly (DataDome) and never solve CAPTCHAs.",
                  "Return only items the tool returned; never invent titles, prices, images, URLs or sellers.",
                  "POST exactly one result envelope to callback.url before deadline_at."],
    }


def _dispatch(db: Session, j: EUBotJob, token: str, c: dict, post, attempts: int = 3):
    """POST the task; retry 3× (1 s, 3 s) on network errors / 5xx / 429. Any failure = visible job state."""
    payload, err = task_envelope(j, token, c["public_base_url"]), None
    for i in range(attempts):
        try:
            r = post(os.environ["GROK_ROUTINE_WEBHOOK_URL"], json=payload, timeout=20,
                     headers={"Authorization": f"Bearer {os.environ['GROK_ROUTINE_WEBHOOK_KEY']}"})
            if r.status_code < 300:
                j.status, j.sent_at, err = "sent", datetime.utcnow(), None
                break
            err = f"webhook HTTP {r.status_code}: {str(r.text)[:200]}"
            if r.status_code < 500 and r.status_code != 429:
                break
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
        if i < attempts - 1:
            time.sleep((1, 3)[min(i, 1)])
    if err:
        j.status, j.finished_at, j.error_code, j.error_detail = "failed", datetime.utcnow(), "webhook_failed", f"Không gọi được Grok Bot: {err}"[:2000]
    db.commit()


def run_serp(job_id: str) -> dict:
    """Background body of a serp job (own DB session). Quota / provider errors end as `failed` with the reason."""
    from ..visual.lens import LensError
    from . import allegro_serp

    with SessionLocal() as db:
        j = db.get(EUBotJob, job_id)
        if not j or j.status != "running":
            return job_dict(j) if j else {}
        try:
            res = allegro_serp.search(j.keyword, j.limit)
        except LensError as e:
            j.status, j.finished_at, j.error_code, j.error_detail = "failed", datetime.utcnow(), e.code[:32], f"SerpApi: {e.detail or e.code}"[:2000]
            db.commit()
            return job_dict(j)
        ids = upsert_items(db, res["items"], query=j.keyword) if res["items"] else []
        j.items_received, j.listing_ids, j.items_written = len(res["items"]), ids, len(ids)
        j.status, j.finished_at = "done" if ids else "empty", datetime.utcnow()
        db.commit()
        return job_dict(j)


def verify(j: EUBotJob | None, token: str | None, secret: str | None) -> bool:
    want = os.getenv("ALLEGRO_CALLBACK_SECRET") or ""
    return bool(j and token and want and secret
                and hmac.compare_digest(hashlib.sha256(token.encode()).hexdigest(), j.token_hash)
                and hmac.compare_digest(secret, want))


def verify_tool(auth: str | None) -> bool:
    want = os.getenv("GROK_TOOL_TOKEN") or ""
    got = (auth or "").removeprefix("Bearer ").strip()
    return bool(want and got and hmac.compare_digest(got, want))


def _clean(raw: dict) -> dict | None:
    """Agent row with a direct allegro.pl offer → listing (numeric offer id required; foreign links replaced)."""
    it = map_item(raw) if isinstance(raw, dict) else None
    if not it or not str(it["source_product_id"]).isdigit():
        return None
    host = (urlparse(it["product_url"]).hostname or "").lower()
    if host != "allegro.pl" and not host.endswith(".allegro.pl"):
        it["product_url"] = f"https://allegro.pl/oferta/{it['source_product_id']}"
    it["data_access"] = "browser_agent"
    return it


def _known_tool_ids(db: Session, sids: list[str]) -> dict[str, int]:
    """Tool results the agent echoes back ("gshop:…" ids) are accepted only if our tool really stored them — the agent can
    select and annotate, never add products."""
    from .models import EUListing

    if not sids:
        return {}
    rows = db.execute(select(EUListing.source_product_id, EUListing.id).where(
        EUListing.source == SOURCE, EUListing.market_country == "PL", EUListing.source_product_id.in_(sids))).all()
    return dict(rows)


def handle_callback(db: Session, j: EUBotJob, body: dict) -> dict:
    """Result envelope → job final state. A second callback for a finished job is acknowledged, not re-applied."""
    if j.status in ("done", "empty"):
        return {**job_dict(j), "already": True}
    status = BOT_STATUS.get(str(body.get("status") or "").lower().strip())
    items = body.get("items") if isinstance(body.get("items"), list) else []
    if status is None:
        status = "done" if items else "failed"
    now = datetime.utcnow()
    j.items_received = len(items)
    if status == "done":
        rows = [r for r in items[:MAX_ITEMS] if isinstance(r, dict)]
        sid = lambda r: str(r.get("source_product_id") or r.get("offer_id") or "")  # noqa: E731
        known = _known_tool_ids(db, [sid(r) for r in rows if sid(r).startswith("gshop:")])
        ids = list(dict.fromkeys(known[sid(r)] for r in rows if sid(r) in known))
        clean, seen = [], set()
        for it in (_clean(r) for r in rows if not sid(r).startswith("gshop:")):
            if it and it["source_product_id"] not in seen:
                seen.add(it["source_product_id"])
                it["raw"] = {**(it.get("raw") or {}), "via": "grok_bot", "agent_note": str(body.get("message") or "")[:500]}
                clean.append(it)
        ids += [i for i in (upsert_items(db, clean, query=j.keyword, now=now) if clean else []) if i not in ids]
        j.listing_ids, j.items_written = ids, len(ids)
        status = "done" if ids else ("empty" if not items else "failed")
        if status == "failed":
            j.error_code, j.error_detail = "invalid_items", f"Agent gửi {len(items)} dòng nhưng không dòng nào hợp lệ."
    elif status == "failed":
        j.error_code, j.error_detail = "bot_error", f"Grok Bot báo lỗi: {str(body.get('message') or '')[:500]}"
    else:
        j.error_code = status
        if body.get("message"):
            j.error_detail = str(body["message"])[:500]
    j.status, j.finished_at = status, now
    db.commit()
    return job_dict(j)


def finish_bridge(db: Session, j: EUBotJob, status: str, received: int, ids: list[int], error: str | None = None) -> dict:
    """Extension report → bridge job final state. Caller checked provider == bridge and status in LIVE (after expire)."""
    st = BRIDGE_STATUS[status]
    j.items_received, j.listing_ids, j.items_written = received, ids, len(ids)
    if st == "done" and not ids:
        st = "empty" if not received else "failed"
        if st == "failed":
            j.error_code, j.error_detail = "invalid_items", f"Extension gửi {received} dòng nhưng không dòng nào hợp lệ."
    elif st == "failed":
        j.error_code, j.error_detail = "bridge_error", f"Extension báo lỗi: {(error or '')[:500]}"
    elif st != "done":
        j.error_code, j.error_detail = st, (error or "")[:500] or None
    j.status, j.finished_at = st, datetime.utcnow()
    db.commit()
    return job_dict(j)
