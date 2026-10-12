"""Live web image discovery — SerpApi Google Lens (https://serpapi.com/google-lens-api) + ImageSearchQuotaManager.

A picture that was never collected can still be searched on the open web: upload it to SerpApi's Image API
(POST https://serpapi.com/image, multipart `image` ≤ 500 KB, `image_id` valid 10 min) or pass a public image `url`,
then GET https://serpapi.com/search.json?engine=google_lens&image_id|url&type&country&hl[&q]. One request per new
(image, parameters); everything else is served from `lens_searches` (cache = job).

Quota is a hard backend limit (IMAGE_SEARCH_MONTHLY_BUDGET / DAILY_BUDGET), reserved under a Postgres advisory lock
before the call, logged in `external_api_calls`; identical concurrent requests share one job (single-flight). Errors
(401 / 403 / 429 / timeout) are not retried, never fabricate results, never contain the API key.
Nothing here runs from the scheduler: only an explicit user search spends quota (IMAGE_SEARCH_AUTO_REFRESH is honoured
by never refreshing).
"""
from __future__ import annotations

import hashlib
import io
import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from urllib.parse import urlparse

import httpx
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ..db import IS_SQLITE, SessionLocal, engine
from .models import ExternalApiCall, LensSearch
from .service import EXACT, EXACT_DIST, LIKELY_SAME, SIMILAR, SIMILAR_DIST, ImageError, QueryImage, fetch_image, hamming

log = logging.getLogger("app.visual.lens")
PROVIDER = "serpapi_lens"
UPLOAD_URL = "https://serpapi.com/image"
SEARCH_URL = "https://serpapi.com/search.json"
ACCOUNT_URL = "https://serpapi.com/account.json"
UPLOAD_MAX = 500 * 1024
# measured 2026-10-09 with the Account API (this_month_usage before/after one upload): see docs/IMAGE_SEARCH.md §Live
UPLOAD_COUNTS = os.getenv("SERPAPI_UPLOAD_COUNTS", "0").lower() in ("1", "true", "yes")
VERIFY_TOP = int(os.getenv("IMAGE_SEARCH_VERIFY_TOP", "40"))  # result thumbnails hashed / embedded to check the match


def _flag(name: str, default: str = "true") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


def api_key() -> str | None:
    return (os.getenv("SERPAPI_API_KEY") or "").strip() or None


def config() -> dict:
    return {"global_enabled": _flag("GLOBAL_IMAGE_SEARCH_ENABLED"), "live_enabled": _flag("IMAGE_SEARCH_LIVE_ENABLED"),
            "local_enabled": _flag("IMAGE_SEARCH_LOCAL_ENABLED"), "provider": os.getenv("IMAGE_SEARCH_PROVIDER", PROVIDER),
            "monthly_budget": int(os.getenv("IMAGE_SEARCH_MONTHLY_BUDGET", "250")), "daily_budget": int(os.getenv("IMAGE_SEARCH_DAILY_BUDGET", "8")),
            "cache_ttl_hours": int(os.getenv("IMAGE_SEARCH_CACHE_TTL_HOURS", "168")), "auto_refresh": _flag("IMAGE_SEARCH_AUTO_REFRESH", "false"),
            "api_key_configured": api_key() is not None, "upload_counts": UPLOAD_COUNTS}


class LensError(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code, self.detail = code, detail


def _scrub(s: str) -> str:
    k = api_key()
    s = re.sub(r"api_key=[^&\s\"']+", "api_key=***", s or "")
    return s.replace(k, "***") if k else s


def live_unavailable() -> str | None:
    c = config()
    if not c["global_enabled"]:
        return "image_search_disabled"
    if not c["live_enabled"]:
        return "live_search_disabled"
    if c["provider"] != PROVIDER:
        return "unsupported_provider"
    if not c["api_key_configured"]:
        return "missing_api_key"
    return None


# ------------------------------------------------------------------ cache key / locks
def cache_key(sha256: str, crop, country: str | None, hl: str, typ: str, q: str | None) -> str:
    crop_s = ",".join(f"{v:.3f}" for v in crop) if crop else ""
    raw = "|".join([PROVIDER, sha256, crop_s, (country or "").lower(), hl.lower(), typ, (q or "").strip().lower()])
    return hashlib.sha256(raw.encode()).hexdigest()


_tlocks: dict[str, threading.Lock] = {}
_tlocks_guard = threading.Lock()


class _lock:
    """Cross-process lock: Postgres advisory lock on its own connection (+ a thread lock), SQLite: thread lock only."""

    def __init__(self, name: str):
        self.name = name
        self.k = int(hashlib.sha256(name.encode()).hexdigest()[:8], 16) & 0x7FFFFFFF

    def __enter__(self):
        with _tlocks_guard:
            self.t = _tlocks.setdefault(self.name, threading.Lock())
        self.t.acquire()
        self.conn = None
        if not IS_SQLITE:
            self.conn = engine.connect()
            self.conn.execute(text("SELECT pg_advisory_lock(727100, :k)"), {"k": self.k})
            self.conn.commit()
        return self

    def __exit__(self, *a):
        try:
            if self.conn is not None:
                self.conn.execute(text("SELECT pg_advisory_unlock(727100, :k)"), {"k": self.k})
                self.conn.commit()
                self.conn.close()
        finally:
            self.t.release()


# ------------------------------------------------------------------ quota manager
def _month_start(now: datetime) -> datetime:
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def usage(db: Session, now: datetime | None = None) -> dict:
    now = now or datetime.utcnow()
    m0, d0 = _month_start(now), now.replace(hour=0, minute=0, second=0, microsecond=0)

    def cnt(*conds, pool=False):  # pool: Lens + Shopping share the SerpApi monthly budget (pricing/quota.py)
        prov = ExternalApiCall.provider.in_((PROVIDER, "serpapi_shopping")) if pool else ExternalApiCall.provider == PROVIDER
        return db.scalar(select(func.count(ExternalApiCall.id)).where(prov, *conds)) or 0

    return {"month_used": cnt(ExternalApiCall.counted.is_(True), ExternalApiCall.created_at >= m0, pool=True),
            "day_used": cnt(ExternalApiCall.counted.is_(True), ExternalApiCall.created_at >= d0),
            "month_errors": cnt(ExternalApiCall.ok.is_(False), ExternalApiCall.kind.in_(("search", "upload")), ExternalApiCall.created_at >= m0),
            "month_cache_hits": cnt(ExternalApiCall.kind == "cache_hit", ExternalApiCall.created_at >= m0),
            "month_refused": cnt(ExternalApiCall.kind == "refused", ExternalApiCall.created_at >= m0)}


def reserve(n: int, search_id: int | None, kinds: list[str]) -> list[int]:
    """Hard budget check + reservation in one critical section (all processes). Returns ExternalApiCall ids; raises
    LensError(quota_exceeded_*) and logs a `refused` row when the call would cross a budget."""
    cfg = config()
    with _lock("serpapi-budget"), SessionLocal() as db:
        u = usage(db)
        why = "quota_exceeded_daily" if u["day_used"] + n > cfg["daily_budget"] else \
            "quota_exceeded_monthly" if u["month_used"] + n > cfg["monthly_budget"] else None
        if why:
            db.add(ExternalApiCall(provider=PROVIDER, kind="refused", counted=False, ok=False, error_code=why, search_id=search_id))
            db.commit()
            raise LensError(why, f"used {u['day_used']}/{cfg['daily_budget']} today, {u['month_used']}/{cfg['monthly_budget']} this month")
        rows = [ExternalApiCall(provider=PROVIDER, kind=k, counted=(k == "search" or (k == "upload" and UPLOAD_COUNTS)), search_id=search_id) for k in kinds]
        db.add_all(rows)
        db.commit()
        return [r.id for r in rows]


def _finish_call(call_id: int, ok: bool, status: int | None, ms: int, code: str | None = None, counted: bool | None = None):
    with SessionLocal() as db:
        c = db.get(ExternalApiCall, call_id)
        c.ok, c.http_status, c.duration_ms, c.error_code = ok, status, ms, code
        if counted is not None:  # request never reached SerpApi (DNS / connect error) → it cannot have consumed quota
            c.counted = counted
        db.commit()


_account: dict = {"at": 0.0, "data": None}


def account(force: bool = False) -> dict | None:
    """SerpApi Account API (free, not counted): real plan usage, cached 10 min."""
    if not api_key():
        return None
    if not force and _account["data"] is not None and time.time() - _account["at"] < 600:
        return _account["data"]
    try:
        r = httpx.get(ACCOUNT_URL, params={"api_key": api_key()}, timeout=15)
        j = r.json()
        data = {k: j.get(k) for k in ("account_status", "plan_name", "searches_per_month", "plan_searches_left", "total_searches_left",
                                      "this_month_usage", "this_hour_searches", "account_rate_limit_per_hour", "plan_renewal_date")}
        data["synced_at"] = datetime.utcnow().isoformat() + "Z"
        if j.get("error"):
            data["error"] = _scrub(str(j["error"]))[:200]
    except Exception as e:  # noqa: BLE001
        data = {"error": _scrub(f"{type(e).__name__}: {e}")[:200], "synced_at": datetime.utcnow().isoformat() + "Z"}
    _account.update(at=time.time(), data=data)
    return data


def quota(db: Session) -> dict:
    cfg = config()
    u = usage(db)
    m0 = _month_start(datetime.utcnow())
    discovered = db.scalar(select(func.coalesce(func.sum(LensSearch.result_count), 0)).where(LensSearch.status == "ok", LensSearch.created_at >= m0)) or 0
    searches = db.scalar(select(func.count(LensSearch.id)).where(LensSearch.created_at >= m0)) or 0
    return {**cfg, **u, "month_remaining": max(0, cfg["monthly_budget"] - u["month_used"]), "day_remaining": max(0, cfg["daily_budget"] - u["day_used"]),
            "month_discovered_results": int(discovered), "month_searches": searches, "live_unavailable": live_unavailable(), "account": account()}


# ------------------------------------------------------------------ SerpApi calls
def upload_bytes(q: QueryImage) -> bytes:
    """Cropped picture ≤ 1024 px JPEG, quality stepped down until ≤ 500 KB (Image API limit)."""
    im = q.upload_src.copy()
    im.thumbnail((1024, 1024))
    for quality in (88, 80, 70, 60, 50):
        b = io.BytesIO()
        im.save(b, "JPEG", quality=quality)
        if b.tell() <= UPLOAD_MAX:
            return b.getvalue()
    im.thumbnail((640, 640))
    b = io.BytesIO()
    im.save(b, "JPEG", quality=70)
    return b.getvalue()


def _http_fail(r: httpx.Response) -> LensError:
    msg = ""
    try:
        msg = str(r.json().get("error") or "")
    except ValueError:
        msg = r.text[:200]
    code = {401: "unauthorized", 403: "forbidden", 429: "rate_limited"}.get(r.status_code, "provider_error" if r.status_code >= 500 else "bad_request")
    if "run out of searches" in msg.lower() or "plan" in msg.lower() and "limit" in msg.lower():
        code = "provider_quota_exhausted"
    return LensError(code, _scrub(f"HTTP {r.status_code}: {msg}")[:300])


def _call(call_id: int, fn):
    t = time.time()
    try:
        r = fn()
    except httpx.ConnectError as e:
        _finish_call(call_id, False, None, int((time.time() - t) * 1000), "network_error", counted=False)
        raise LensError("network_error", _scrub(str(e))[:200])
    except httpx.TimeoutException:
        _finish_call(call_id, False, None, int((time.time() - t) * 1000), "timeout")  # may still have been charged
        raise LensError("timeout", "SerpApi did not answer in time")
    ms = int((time.time() - t) * 1000)
    if r.status_code != 200:
        err = _http_fail(r)
        _finish_call(call_id, False, r.status_code, ms, err.code, counted=False if r.status_code in (401, 403) else None)
        raise err
    try:
        j = r.json()
    except ValueError:
        _finish_call(call_id, False, r.status_code, ms, "bad_response")
        raise LensError("bad_response", "non-JSON answer")
    if j.get("error") and not (j.get("visual_matches") or j.get("exact_matches")):
        e = str(j["error"])
        code = "no_results" if "hasn't returned any results" in e.lower() else "provider_error"
        _finish_call(call_id, code == "no_results", r.status_code, ms, None if code == "no_results" else code)
        if code == "no_results":
            return {"visual_matches": [], "exact_matches": [], "search_metadata": j.get("search_metadata"), "error": e}
        raise LensError(code, _scrub(e)[:300])
    _finish_call(call_id, True, r.status_code, ms)
    return j


def serp_upload(data: bytes, call_id: int) -> str:
    j = _call(call_id, lambda: httpx.post(UPLOAD_URL, data={"api_key": api_key()}, files={"image": ("query.jpg", data, "image/jpeg")}, timeout=30))
    if not j.get("image_id"):
        raise LensError("upload_failed", _scrub(str(j))[:200])
    return j["image_id"]


def serp_lens(params: dict, call_id: int) -> dict:
    p = {"engine": "google_lens", "api_key": api_key(), **{k: v for k, v in params.items() if v not in (None, "")}}
    return _call(call_id, lambda: httpx.get(SEARCH_URL, params=p, timeout=60))


# ------------------------------------------------------------------ normalize
_SYM = [("US$", "USD"), ("A$", "AUD"), ("AU$", "AUD"), ("C$", "CAD"), ("CA$", "CAD"), ("NZ$", "NZD"), ("HK$", "HKD"), ("S$", "SGD"),
        ("€", "EUR"), ("£", "GBP"), ("₱", "PHP"), ("₫", "VND"), ("zł", "PLN"), ("Kč", "CZK"), ("lei", "RON"), ("Ft", "HUF"), ("₹", "INR"),
        ("¥", None), ("kr", None)]
_ISO = ("EUR", "USD", "GBP", "PLN", "PHP", "AUD", "NZD", "CAD", "SEK", "NOK", "DKK", "CZK", "RON", "HUF", "CHF", "VND", "AED", "SAR", "BGN", "TRY")
_DOLLAR = {"us": "USD", "au": "AUD", "ca": "CAD", "nz": "NZD", "sg": "SGD", "hk": "HKD"}
_KR = {"se": "SEK", "no": "NOK", "dk": "DKK"}


def currency_of(s: str | None, country: str | None) -> str | None:
    """ISO currency only when the text says it unambiguously ('$' needs the search country; never guessed)."""
    if not s:
        return None
    s = str(s)
    up = s.upper()
    for iso in _ISO:
        if re.search(rf"\b{iso}\b", up):
            return iso
    c = (country or "").lower()
    for sym, iso in _SYM:
        if sym in s:
            if sym == "kr":
                return _KR.get(c)
            return iso
    if "$" in s:
        return _DOLLAR.get(c)
    return None


def norm_link(u: str | None) -> str | None:
    if not u:
        return None
    p = urlparse(u)
    return f"{(p.hostname or '').lower().removeprefix('www.')}{p.path.rstrip('/')}" or None


def _price(it: dict, country: str | None) -> tuple[float | None, str | None, str | None]:
    p = it.get("price")
    if isinstance(p, dict):
        raw, val, sym = p.get("value"), p.get("extracted_value"), p.get("currency")
    else:
        raw, val, sym = (p if isinstance(p, str) else None), it.get("extracted_price"), None
    try:
        val = float(val) if val is not None else None
    except (TypeError, ValueError):
        val = None
    return val, currency_of(sym or raw, country), raw


def normalize(j: dict, country: str | None, now: datetime) -> list[dict]:
    """exact_matches + visual_matches → one list (deduped by link, exact wins). Missing fields stay None."""
    out: dict[str, dict] = {}
    for arr, match in (("exact_matches", "exact_match"), ("visual_matches", "visual_similarity")):
        for it in j.get(arr) or []:
            link = it.get("link")
            key = norm_link(link) or it.get("image") or it.get("thumbnail")
            if not key or key in out:
                continue
            val, cur, raw = _price(it, country)
            host = (urlparse(link).hostname or "").lower().removeprefix("www.") if link else None
            out[key] = {
                "source": "google_lens", "discovery_provider": "serpapi", "search_origin": "live_web", "title": it.get("title"),
                "image_url": it.get("image"), "thumbnail_url": it.get("thumbnail"), "product_url": link, "shop_name": it.get("source"),
                "marketplace": host, "source_icon": it.get("source_icon"), "country": None, "price": val, "currency": cur, "price_raw": raw,
                "price_verified": False, "price_source": "search_result" if val is not None else None, "rating": it.get("rating"),
                "reviews": it.get("reviews"), "in_stock": it.get("in_stock") if it.get("in_stock") is not None else (False if it.get("out_of_stock") else None),
                "condition": it.get("condition"), "lens_position": it.get("position"), "lens_array": arr, "match_type": match,
                "observed_at": now.isoformat(), "verification_status": "unverified", "hash_distance": None, "cosine": None,
                "tier": LIKELY_SAME if match == "exact_match" else SIMILAR, "similarity": None, "link_key": norm_link(link),
            }
    return list(out.values())


def verify_matches(q: QueryImage, items: list[dict]) -> None:
    """Download the first VERIFY_TOP result thumbnails (small, SSRF-guarded) and measure them against the query with the
    local engine: hash distance (+ CLIP cosine when on). Tier from measured similarity, never from Lens position."""
    todo = [i for i in items[:VERIFY_TOP] if i.get("thumbnail_url") or i.get("image_url")]

    def get(i):
        for u in (i.get("thumbnail_url"), i.get("image_url")):
            if not u:
                continue
            try:
                return i, QueryImage(fetch_image(u, max_bytes=2 * 1024 * 1024, timeout=8, max_redirects=2))
            except Exception:  # noqa: BLE001
                continue
        return i, None

    with ThreadPoolExecutor(8) as ex:
        got = [(i, r) for i, r in ex.map(get, todo) if r is not None]
    emb = None
    try:
        from . import embed

        if embed.enabled() and got:
            qv = q.embedding if q.embedding is not None else embed.embed_image(q.im)
            vs = embed.embed_batch([r.im for _, r in got])
            emb = {id(i): float(v @ qv) for (i, _), v in zip(got, vs)}
    except Exception as e:  # noqa: BLE001
        log.warning("lens verify: embedding skipped: %s", str(e)[:120])
    for i, r in got:
        d = min(hamming(q.phash, r.phash), hamming(q.dhash, r.dhash))
        cos = emb.get(id(i)) if emb else None
        i.update(hash_distance=d, cosine=round(cos, 4) if cos is not None else None, verification_status="visual_verified")
        try:
            from .embed import SAME_COS, SIMILAR_COS
        except Exception:  # noqa: BLE001
            SAME_COS, SIMILAR_COS = 0.95, 0.85
        if d <= EXACT_DIST:
            i["tier"], i["similarity"] = EXACT, round(1 - d / 64, 3)
        elif (cos is not None and cos >= SAME_COS) or (i["match_type"] == "exact_match" and ((cos is not None and cos >= SIMILAR_COS) or d <= SIMILAR_DIST)):
            i["tier"], i["similarity"] = LIKELY_SAME, round(cos if cos is not None else 1 - d / 64, 3)
        else:
            i["tier"], i["similarity"] = SIMILAR, round(cos if cos is not None else 1 - d / 64, 3)


def mark_in_database(db: Session, items: list[dict]) -> None:
    """Results whose product page is already in SpyAds (saved web result / EU listing, or an ad's landing page)."""
    from ..eu_market.models import EUListing
    from ..models import Ad

    keys = {i["link_key"] for i in items if i.get("link_key")}
    if not keys:
        return
    hosts = {k.split("/")[0] for k in keys}
    known: dict[str, dict] = {}
    for lid, pid, url in db.execute(select(EUListing.id, EUListing.product_id, EUListing.url).where(EUListing.url.is_not(None),
                                                                                                 or_any_host(EUListing.url, hosts))).all():
        k = norm_link(url)
        if k in keys:
            known[k] = {"eu_listing_id": lid, "eu_product_id": pid}
    for aid, pid, url in db.execute(select(Ad.id, Ad.product_id, Ad.landing_url).where(Ad.landing_url.is_not(None), or_any_host(Ad.landing_url, hosts))).all():
        k = norm_link(url)
        if k in keys and k not in known:
            known[k] = {"ad_id": aid, "product_id": pid}
    for i in items:
        hit = known.get(i.get("link_key"))
        i["in_database"] = bool(hit)
        if hit:
            i.update(hit)


def or_any_host(col, hosts: set[str]):
    from sqlalchemy import or_

    return or_(*[col.contains(h) for h in list(hosts)[:60]])


# ------------------------------------------------------------------ job
def start(q: QueryImage, *, country: str | None, hl: str = "en", typ: str = "all", lens_q: str | None = None, image_url: str | None = None,
          cache_only: bool = False) -> dict:
    """Cache hit → finished job at once (no quota). cache_only → never call the provider. Otherwise one job per cache key
    (single-flight across users / processes) running in a thread. Returns {job_id, status, cached} or raises LensError."""
    if typ not in ("all", "products", "visual_matches", "exact_matches"):
        raise LensError("bad_type", typ)
    key = cache_key(q.sha256, q.crop, country, hl, typ, lens_q if typ != "exact_matches" else None)
    now = datetime.utcnow()
    with _lock(f"lens:{key}"), SessionLocal() as db:
        hit = db.scalar(select(LensSearch).where(LensSearch.cache_key == key, LensSearch.status == "ok", LensSearch.expires_at > now)
                        .order_by(LensSearch.id.desc()).limit(1))
        if hit:
            db.add(ExternalApiCall(provider=PROVIDER, kind="cache_hit", counted=False, ok=True, search_id=hit.id))
            db.commit()
            return {"job_id": hit.id, "status": "ok", "cached": True}
        running = db.scalar(select(LensSearch).where(LensSearch.cache_key == key, LensSearch.status == "running",
                                                     LensSearch.created_at > now - timedelta(minutes=3)).order_by(LensSearch.id.desc()).limit(1))
        if running:
            return {"job_id": running.id, "status": "running", "cached": False, "joined": True}
        if cache_only:
            return {"job_id": None, "status": "not_cached", "cached": False}
        why = live_unavailable()
        if why:
            raise LensError(why)
        s = LensSearch(cache_key=key, image_sha256=q.sha256, provider=PROVIDER, status="running",
                       params={"country": country, "hl": hl, "type": typ, "q": lens_q, "crop": list(q.crop) if q.crop else None,
                               "input": "url" if image_url else "upload"})
        db.add(s)
        db.commit()
        sid = s.id
    threading.Thread(target=_run, args=(sid, q, country, hl, typ, lens_q, image_url), name=f"lens-{sid}", daemon=True).start()
    return {"job_id": sid, "status": "running", "cached": False}


def _run(sid: int, q: QueryImage, country, hl, typ, lens_q, image_url):
    now = datetime.utcnow()
    calls = 0
    try:
        kinds = ["search"] if image_url else ["upload", "search"]
        ids = reserve(sum(1 for k in kinds if k == "search" or UPLOAD_COUNTS), sid, kinds)
        params = {"type": typ, "country": (country or "").lower() or None, "hl": hl, "q": lens_q if typ != "exact_matches" else None}
        if image_url:
            params["url"] = image_url
        else:
            params["image_id"] = serp_upload(upload_bytes(q), ids[0])
            calls += 1
        try:
            from . import embed

            if embed.enabled() and q.embedding is None:
                q.embedding = embed.embed_image(q.im)
        except Exception:  # noqa: BLE001
            pass
        j = serp_lens(params, ids[-1])
        calls += 1
        items = normalize(j, country, now)
        verify_matches(q, items)
        with SessionLocal() as db:
            mark_in_database(db, items)
            s = db.get(LensSearch, sid)
            s.results, s.result_count, s.api_calls, s.status = items, len(items), calls, "ok"
            s.raw = {k: j.get(k) for k in ("search_metadata", "search_parameters", "exact_matches", "visual_matches", "related_content", "error") if k in j}
            if s.raw.get("search_parameters"):
                s.raw["search_parameters"].pop("api_key", None)
            s.finished_at = datetime.utcnow()
            s.expires_at = s.finished_at + timedelta(hours=config()["cache_ttl_hours"])
            _retention(db)
            db.commit()
        log.info("lens search %s: %s results (%s API calls)", sid, len(items), calls)
    except LensError as e:
        _fail(sid, e.code, e.detail, calls)
    except Exception as e:  # noqa: BLE001
        log.exception("lens search %s failed", sid)
        _fail(sid, "internal_error", _scrub(str(e))[:300], calls)


def _fail(sid: int, code: str, detail: str, calls: int):
    with SessionLocal() as db:
        s = db.get(LensSearch, sid)
        s.status, s.error_code, s.error_detail, s.api_calls, s.finished_at = "error", code, detail, calls, datetime.utcnow()
        db.commit()
    log.warning("lens search %s: %s %s", sid, code, detail)


def _retention(db: Session):
    days = int(os.getenv("LENS_RAW_RETENTION_DAYS", "30"))
    db.execute(LensSearch.__table__.update().where(LensSearch.raw.is_not(None), LensSearch.created_at < datetime.utcnow() - timedelta(days=days)).values(raw=None))


# ------------------------------------------------------------------ result views
def job_view(s: LensSearch, offset: int = 0, limit: int = 60, keywords: list[str] | None = None) -> dict:
    rows = [dict(r, index=n) for n, r in enumerate(s.results or [])]  # `index` = stored position: what verify / save take
    if keywords:  # image + keywords: matching titles first, nothing dropped, no extra API call
        for r in rows:
            t = (r.get("title") or "").lower()
            r["keyword_hits"] = [k for k in keywords if all(w in t for w in k.split())]
        rows.sort(key=lambda r: -len(r["keyword_hits"]))
    tiers = {t: sum(1 for r in rows if r["tier"] == t) for t in (EXACT, LIKELY_SAME, SIMILAR)}
    return {"job_id": s.id, "status": s.status, "error_code": s.error_code, "error": s.error_detail, "params": s.params,
            "created_at": s.created_at, "finished_at": s.finished_at, "expires_at": s.expires_at, "api_calls": s.api_calls,
            "total_listings": len(rows), "tiers": tiers, "in_database": sum(1 for r in rows if r.get("in_database")),
            "discovered": sum(1 for r in rows if not r.get("in_database")), "with_price": sum(1 for r in rows if r.get("price") is not None),
            "offset": offset, "limit": limit, "rows": rows[offset:offset + limit]}


def as_compare_rows(s: LensSearch, fx: dict) -> list[dict]:
    """Web results in the shape service.compare_prices() expects — price is 'observed in search results', not verified."""
    from ..eu_market.service import to_eur

    out = []
    for n, r in enumerate(s.results or []):
        out.append({"key": f"web:{s.id}:{n}", "kind": "web", "source": "google_lens", "source_label": r.get("shop_name") or r.get("marketplace") or "Web",
                    "country": r.get("country"), "title": r.get("title"), "thumb": r.get("thumbnail_url") or r.get("image_url"), "image_url": r.get("image_url"),
                    "tier": r["tier"], "similarity": r.get("similarity"), "hash_distance": r.get("hash_distance"), "cosine": r.get("cosine"),
                    "price": r.get("price"), "currency": r.get("currency"), "price_raw": r.get("price_raw"),
                    "price_eur": to_eur(r.get("price"), r.get("currency"), fx) if r.get("price") is not None else None,
                    "price_source": "verified_page" if r.get("price_verified") else "search_result", "price_verified": bool(r.get("price_verified")),
                    "price_includes_vat": r.get("price_includes_vat"), "shipping_price": None, "price_basis": "search_result",
                    "seller": r.get("shop_name"), "marketplace": r.get("marketplace"), "url": r.get("product_url"), "observed_at": datetime.fromisoformat(r["verified_at"] if r.get("verified_at") else r["observed_at"]),
                    "first_seen_at": None, "active": r.get("in_stock"), "funnel": None, "creative_id": None, "ad_id": r.get("ad_id"),
                    "product_id": r.get("product_id"), "eu_listing_id": r.get("eu_listing_id"), "eu_product_id": r.get("eu_product_id"),
                    "web_job_id": s.id, "web_index": n, "in_database": r.get("in_database"), "search_origin": "live_web"})
    return out


def verify_price(db: Session, s: LensSearch, idx: int) -> dict:
    """Open the result's product page (public: JSON-LD, else price meta tags; robots.txt respected, no anti-bot bypass) and
    replace the observed search price with the page's own when it has one. The outcome is stored on the row
    (`price_check`: ok + code + `reason` in plain words) so the UI can show it next to the card."""
    from ..eu_market.connectors import jsonld
    from ..eu_market.connectors.base import ConnectorError

    rows = list(s.results or [])
    if not 0 <= idx < len(rows):
        raise LensError("not_found", f"result #{idx} does not exist in job {s.id} ({len(rows)} results)")
    r = dict(rows[idx])
    if not r.get("product_url"):
        raise LensError("no_product_page", "kết quả này không có link trang sản phẩm")
    now = datetime.utcnow().isoformat()
    try:
        it = jsonld.fetch_product(r["product_url"])
    except ConnectorError as e:
        reason = {"access_restricted": "Shop chặn truy cập tự động hoặc robots.txt không cho phép — mở trang để xem giá",
                  "unsupported_capability": "Trang không công bố dữ liệu sản phẩm / giá (có thể là bài viết, video hoặc trang danh mục)",
                  "rate_limited": "Shop đang giới hạn tốc độ — thử lại sau", "temporarily_unavailable": "Không tải được trang (lỗi mạng hoặc shop không phản hồi)",
                  "bad_response": "Shop trả lỗi khi mở trang"}.get(e.code, e.code)
        r["price_check"] = {"ok": False, "code": e.code, "detail": (e.detail or "")[:160], "reason": reason, "at": now}
    else:
        if it.get("price") is None:
            r["price_check"] = {"ok": False, "code": "no_price", "reason": "Trang có thông tin sản phẩm nhưng không công bố giá", "at": now}
        else:
            r["price_check"] = {"ok": True, "reason": "Giá đọc từ trang sản phẩm", "at": now, "observed_before": r.get("price"), "observed_currency": r.get("currency"),
                                "source": it.get("data_access")}
            r.update(price=it["price"], currency=it.get("currency") or r.get("currency"), price_verified=True, price_source="verified_page",
                     price_includes_vat=it.get("price_includes_vat"), verified_at=now,
                     in_stock={"in_stock": True, "out_of_stock": False}.get(it.get("availability"), r.get("in_stock")),
                     ean=it.get("ean"), brand=it.get("brand"), page_listing=it)
        r["title"] = r.get("title") or it.get("title")
    rows[idx] = r
    s.results = rows  # reassign: JSON column change tracking
    db.commit()
    return dict(r, index=idx)


def save_result(db: Session, s: LensSearch, idx: int) -> dict:
    """Discovered web result → SpyAds index: an EU-Market listing row (source google_lens, country 'WW' unless the page
    says otherwise), its picture hashed / embedded at once so local search finds it next time."""
    from ..eu_market.service import upsert_items
    from .service import index_one

    rows = list(s.results or [])
    if not 0 <= idx < len(rows):
        raise LensError("not_found", "no such result")
    r = rows[idx]
    if r.get("eu_listing_id"):
        return {"eu_listing_id": r["eu_listing_id"], "eu_product_id": r.get("eu_product_id"), "already": True}
    page = r.get("page_listing") or {}
    item = {"source": "google_lens", "source_product_id": hashlib.sha1((r.get("product_url") or r.get("image_url") or "").encode()).hexdigest()[:32],
            "market_country": page.get("market_country") if page.get("market_country") not in (None, "EU") else "WW",
            "title": (r.get("title") or r.get("shop_name") or "web result")[:512], "price": r.get("price") if r.get("currency") else None,
            "currency": r.get("currency"), "price_includes_vat": r.get("price_includes_vat"), "thumbnail_url": r.get("image_url") or r.get("thumbnail_url"),
            "product_url": r.get("product_url"), "seller_name": r.get("shop_name"), "seller_id": r.get("marketplace"), "ean": r.get("ean"),
            "brand": r.get("brand"), "availability": "in_stock" if r.get("in_stock") else "out_of_stock" if r.get("in_stock") is False else "unknown",
            "data_access": "public_page_jsonld" if r.get("price_verified") else "search_result", "confidence": 0.8 if r.get("price_verified") else 0.4,
            "raw": {"lens_search_id": s.id, "lens_index": idx, "match_type": r.get("match_type"), "tier": r.get("tier")}}
    ids = upsert_items(db, [item], query=None)
    if not ids:
        raise LensError("save_failed", "listing not stored")
    from ..eu_market.models import EUListing

    l = db.get(EUListing, ids[0])
    lid, lpid = l.id, l.product_id
    db.commit()  # index_one uses its own session: never hold this one's transaction across it
    pi = None
    try:
        pi = index_one("eu_listing", lid)
    except Exception as e:  # noqa: BLE001
        log.warning("saved web result %s: picture not indexed: %s", lid, str(e)[:120])
    try:
        from . import embed

        if pi is not None and embed.enabled():
            embed.embed_pending(50)
    except Exception:  # noqa: BLE001
        pass
    r = dict(r, eu_listing_id=lid, eu_product_id=lpid, in_database=True)
    rows[idx] = r
    s.results = rows
    db.commit()
    return {"eu_listing_id": lid, "eu_product_id": lpid, "image_indexed": pi is not None, "already": False}
