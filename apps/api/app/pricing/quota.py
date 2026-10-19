"""Per-provider budgets on the shared `external_api_calls` ledger (same lock + rows the Lens quota manager uses).

SerpApi has ONE pool (IMAGE_SEARCH_MONTHLY_BUDGET, 250): Lens + Shopping draw from it; Shopping has its own daily
soft limit so it can never starve image search. Bright Data is a separate pool (BRIGHTDATA_MONTHLY_LIMIT, 5000).
Budgets are SpyAds' own hard limits; the provider's real meter is checked separately (lens.account(), BD has none).
"""
from __future__ import annotations

import os
import time
from datetime import datetime

import httpx
from sqlalchemy import func, select

from ..db import SessionLocal
from ..visual.lens import _lock
from ..visual.models import ExternalApiCall

SERPAPI_POOL = ("serpapi_lens", "serpapi_shopping")  # one monthly budget
BRIGHTDATA = "brightdata"


class QuotaError(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code, self.detail = code, detail


def env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)).split("#")[0].strip())
    except ValueError:
        return default


def budgets(provider: str) -> tuple[tuple[str, ...], int, int]:
    """(providers sharing the monthly pool, monthly, daily) for one provider."""
    if provider in SERPAPI_POOL:
        daily = env_int("IMAGE_SEARCH_DAILY_BUDGET", 8) if provider == "serpapi_lens" else env_int("SERPAPI_SHOPPING_DAILY_LIMIT", 4)
        return SERPAPI_POOL, env_int("IMAGE_SEARCH_MONTHLY_BUDGET", 250), daily
    if provider == "brightdata_posts":  # Web Scraper API datasets (engagement): a different, per-record billed product with its own small pool
        return ("brightdata_posts",), env_int("BRIGHTDATA_POSTS_MONTHLY_LIMIT", 30), env_int("BRIGHTDATA_POSTS_DAILY_LIMIT", 10)
    return (BRIGHTDATA,), env_int("BRIGHTDATA_MONTHLY_LIMIT", 5000), env_int("BRIGHTDATA_DAILY_SOFT_LIMIT", 150)


def used(db, providers: tuple[str, ...], since: datetime) -> int:
    return db.scalar(select(func.count(ExternalApiCall.id)).where(ExternalApiCall.provider.in_(providers), ExternalApiCall.counted.is_(True),
                                                                   ExternalApiCall.created_at >= since)) or 0


def remaining(db, provider: str) -> dict:
    pool, monthly, daily = budgets(provider)
    now = datetime.utcnow()
    m = used(db, pool, now.replace(day=1, hour=0, minute=0, second=0, microsecond=0))
    d = used(db, (provider,), now.replace(hour=0, minute=0, second=0, microsecond=0))
    return {"provider": provider, "month_used": m, "monthly_budget": monthly, "month_remaining": max(0, monthly - m),
            "day_used": d, "daily_budget": daily, "day_remaining": max(0, daily - d)}


def reserve(provider: str, kind: str, n: int = 1, reserve_floor: int = 0) -> int:
    """Budget check + ledger row in one critical section across API + worker. `reserve_floor` keeps that many monthly
    requests back for interactive use (background enrichment must not spend the last ones). Returns the call id."""
    pool, monthly, daily = budgets(provider)
    with _lock(f"{provider}-budget"), SessionLocal() as db:
        r = remaining(db, provider)
        why = "quota_exceeded_daily" if r["day_used"] + n > daily else "quota_exceeded_monthly" if r["month_used"] + n > monthly - reserve_floor else None
        if why:
            db.add(ExternalApiCall(provider=provider, kind="refused", counted=False, ok=False, error_code=why))
            db.commit()
            raise QuotaError(why, f"{provider}: {r['day_used']}/{daily} today, {r['month_used']}/{monthly} this month")
        row = ExternalApiCall(provider=provider, kind=kind, counted=True)
        db.add(row)
        db.commit()
        return row.id


def finish(call_id: int, ok: bool, status: int | None, t0: float, code: str | None = None, counted: bool | None = None):
    with SessionLocal() as db:
        c = db.get(ExternalApiCall, call_id)
        c.ok, c.http_status, c.duration_ms, c.error_code = ok, status, int((time.time() - t0) * 1000), code
        if counted is not None:  # never reached the provider → cannot have been billed
            c.counted = counted
        db.commit()


def call(provider: str, kind: str, fn, reserve_floor: int = 0):
    """reserve → fn() → finish. fn returns an httpx.Response; raises QuotaError / httpx errors to the caller."""
    cid = reserve(provider, kind, 1, reserve_floor)
    t0 = time.time()
    try:
        r = fn()
    except httpx.ConnectError as e:
        finish(cid, False, None, t0, "network_error", counted=False)
        raise QuotaError("network_error", str(e)[:120])
    except httpx.TimeoutException:
        finish(cid, False, None, t0, "timeout")  # may still have been charged
        raise QuotaError("timeout", f"{provider} did not answer in time")
    finish(cid, r.status_code < 400, r.status_code, t0, None if r.status_code < 400 else f"http_{r.status_code}",
           counted=False if r.status_code in (400, 401, 403, 422) else None)  # rejected requests are not billed
    return r
