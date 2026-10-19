"""EU Market jobs — never inside a web request's critical path (spec §5).

  python -m app.eu_market.jobs sync      # watchlist keywords × countries × configured sources (+ one BigBuy page)
  python -m app.eu_market.jobs snapshot  # re-price watched listings (records a snapshot on change / daily)
  python -m app.eu_market.jobs fx        # ECB reference rates
  python -m app.eu_market.jobs match     # supplier + ad matching
  python -m app.eu_market.jobs all

Runs from Windows Task Scheduler as is, and from realtime.scheduler_loop every EU_SYNC_EVERY_MIN when a source is
configured. No DB transaction is held across a network call: run row → commit → fetch → upsert → commit.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import datetime, timedelta

from sqlalchemy import select

from ..db import SessionLocal
from . import analytics, connectors
from .connectors.base import ConnectorError
from .models import EUListing, EUSyncRun, EUWatchlist
from .service import fx_table, refresh_fx, upsert_items

log = logging.getLogger("app.eu_market")
SNAPSHOT_MAX = int(os.getenv("EU_SNAPSHOT_MAX", "200"))
DEFAULT_COUNTRIES = [c.strip().upper() for c in os.getenv("EU_MARKET_COUNTRIES", "DE,FR,IT,ES,NL,BE,PL").split(",") if c.strip()]


def enabled() -> bool:
    return os.getenv("EU_MARKET_ENABLED", "1").lower() not in ("0", "false", "no")


def _start(source: str, query: str | None, country: str | None) -> int:
    with SessionLocal() as db:
        r = EUSyncRun(connector=source, query=query, country=country)
        db.add(r)
        db.commit()
        return r.id


def _finish(run_id: int, *, read=0, written=0, error: ConnectorError | Exception | None = None, cursor=None):
    with SessionLocal() as db:
        r = db.get(EUSyncRun, run_id)
        r.ended_at, r.items_read, r.items_written, r.next_cursor = datetime.utcnow(), read, written, cursor
        if error is None:
            r.status = "ok"
        else:
            r.status = r.error_code = getattr(error, "code", "error")
            r.error_detail = str(error)[:2000]  # connector errors never contain credentials (headers aren't logged)
        db.commit()


async def search_one(source: str, query: str, country: str, cursor: str | None = None) -> dict:
    run = _start(source, query, country)
    try:
        res = await connectors.get(source).search_products(query, country, cursor)
    except Exception as e:  # noqa: BLE001 — every failure becomes a visible sync-run row, never a crash
        _finish(run, error=e)
        return {"source": source, "country": country, "query": query, "error": getattr(e, "code", "error"), "detail": str(e)[:300]}
    with SessionLocal() as db:
        ids = upsert_items(db, res["items"], query=query)
    _finish(run, read=len(res["items"]), written=len(ids), cursor=res.get("next_cursor"))
    return {"source": source, "country": country, "query": query, "read": len(res["items"]), "written": len(ids),
            "next_cursor": res.get("next_cursor"), "listing_ids": ids}


async def run_search(keywords: list[str], countries: list[str] | None = None, sources: list[str] | None = None) -> list[dict]:
    """keywords × countries × sources (only combos the source supports). Unconfigured sources are reported, not skipped silently."""
    countries = countries or DEFAULT_COUNTRIES
    tasks, skipped = [], []
    for s in sources or [c for c, cls in connectors.CONNECTORS.items() if cls.status == "available" and "search" in cls.capabilities]:
        c = connectors.get(s)
        if c.status != "available" or "search" not in c.capabilities:
            skipped.append({"source": s, "error": "unsupported_capability"})
            continue
        if not c.configured():
            skipped.append({"source": s, "error": "missing_credentials", "detail": "set " + ", ".join(c.env_vars)})
            continue
        for country in countries:
            if country in c.countries:
                tasks += [search_one(s, k, country) for k in keywords]
    return skipped + list(await asyncio.gather(*tasks))


async def sync_bigbuy_page() -> dict | None:
    c = connectors.get("bigbuy")
    if not c.configured():
        return None
    with SessionLocal() as db:
        last = db.scalar(select(EUSyncRun).where(EUSyncRun.connector == "bigbuy", EUSyncRun.status == "ok")
                         .order_by(EUSyncRun.started_at.desc()).limit(1))
        if last and datetime.utcnow() - last.started_at < timedelta(minutes=10):
            return {"source": "bigbuy", "skipped": "rate limit window (10 req/hour)"}
        cursor = last.next_cursor if last else None  # None after the last page → start over (refresh)
    run = _start("bigbuy", None, "ES")
    try:
        res = await c.sync_catalog(cursor)
    except Exception as e:  # noqa: BLE001
        _finish(run, error=e)
        return {"source": "bigbuy", "error": getattr(e, "code", "error")}
    with SessionLocal() as db:
        ids = upsert_items(db, res["items"])
    _finish(run, read=len(res["items"]), written=len(ids), cursor=res.get("next_cursor"))
    return {"source": "bigbuy", "page": cursor or "1", "written": len(ids)}


async def sync() -> dict:
    with SessionLocal() as db:
        wls = db.scalars(select(EUWatchlist).where(EUWatchlist.active.is_(True))).all()
        plan = [(w.id, w.keywords or [], w.countries or None, w.sources or None) for w in wls if w.keywords]
    out = []
    for wid, kws, countries, sources in plan:
        out += await run_search(kws, countries, sources)
        with SessionLocal() as db:
            db.get(EUWatchlist, wid).last_synced_at = datetime.utcnow()
            db.commit()
    bb = await sync_bigbuy_page()
    return {"searches": out, "bigbuy": bb}


async def snapshot() -> dict:
    """Re-price listings that belong to a watchlist (product_ids or keyword hits), most stale first."""
    from .service import keyword_filter

    with SessionLocal() as db:
        pids: set[int] = set()
        for w in db.scalars(select(EUWatchlist).where(EUWatchlist.active.is_(True))).all():
            pids |= set(w.product_ids or [])
            if w.keywords:
                pids |= {p for p in db.scalars(select(EUListing.product_id).where(keyword_filter(w.keywords))).all() if p}
        rows = db.scalars(select(EUListing).where(EUListing.product_id.in_(pids or [-1]), EUListing.kind == "marketplace_listing")
                          .order_by(EUListing.last_seen_at).limit(SNAPSHOT_MAX)).all()
        todo = [(l.source, l.url if l.source == "jsonld" else l.source_product_id, l.market_country) for l in rows
                if "get_product" in getattr(connectors.CONNECTORS.get(l.source), "capabilities", ())]  # jsonld re-reads the page; import-only sources (allegro) have nothing to refresh
    done, errors = 0, {}

    async def one(src, sid, country):
        nonlocal done
        try:
            it = await connectors.get(src).get_product(sid, country)
        except ConnectorError as e:
            errors[e.code] = errors.get(e.code, 0) + 1
            return
        with SessionLocal() as db:
            upsert_items(db, [it])
        done += 1

    await asyncio.gather(*(one(*t) for t in todo))
    return {"listings": len(todo), "refreshed": done, "errors": errors}


def fx() -> dict:
    with SessionLocal() as db:
        return refresh_fx(db)


def match() -> dict:
    with SessionLocal() as db:
        res = {"suppliers": analytics.match_suppliers(db), "ads": analytics.match_ads(db)}
    return res


def run_all() -> dict:
    out = {}
    try:
        out["fx"] = fx()
    except Exception as e:  # noqa: BLE001 — ECB down must not block the sync
        out["fx"] = {"error": str(e)[:200]}
    out["sync"] = asyncio.run(sync())
    out["snapshot"] = asyncio.run(snapshot())
    out["match"] = match()
    return out


def any_configured() -> bool:
    return any(cls.status == "available" and cls().configured() for cls in connectors.CONNECTORS.values())


if __name__ == "__main__":
    import json

    from .. import models  # noqa: F401 — register every table before migrating
    from ..db import auto_migrate

    logging.basicConfig(level=logging.INFO)
    auto_migrate()
    cmd =sys.argv[1] if len(sys.argv) > 1 else "all"
    fn = {"sync": lambda: asyncio.run(sync()), "snapshot": lambda: asyncio.run(snapshot()), "fx": fx, "match": match, "all": run_all}[cmd]
    print(json.dumps(fn(), default=str, indent=1)[:5000])
