"""Data & Connectors monitoring (Meta collector upgrade, Phase 5): per source over a time window —
pages fetched, ads accepted / duplicate / invalid (crawl_runs), cursor + retry state (crawl_jobs), health (connectors),
live-search volume (search_jobs.sources) and media availability (creatives by network × status)."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..crawl_queue import CrawlJob, CrawlRun
from ..models import Connector, Creative, SearchJob
from ..platforms import connector_network

COUNTS = ("pages", "empty_pages", "fetched", "accepted", "new_ads", "duplicates", "invalid", "excluded",
          "requests", "cursor_cycles", "cursor_restarts", "schema_drift")


def cursor_summary(checkpoint: dict | None) -> dict | None:
    """TrackedQuery.cursor {"at", "pages": {country: {cursor, done}}} → how far each country got."""
    pages = (checkpoint or {}).get("pages")
    if not isinstance(pages, dict):
        return None
    st = [v for v in pages.values() if isinstance(v, dict)]
    return {"countries": len(st), "resumable": sum(1 for v in st if v.get("cursor") and not v.get("done")),
            "done": sum(1 for v in st if v.get("done")), "at": (checkpoint or {}).get("at")}


def collector_kpis(db: Session, hours: int = 24, now: datetime | None = None) -> dict:
    now = now or datetime.utcnow()
    since = now - timedelta(hours=hours)
    conns = db.scalars(select(Connector).where(Connector.adapter.is_not(None)).order_by(Connector.id)).all()
    jobs: dict[int, list[CrawlJob]] = defaultdict(list)
    for j in db.scalars(select(CrawlJob)):
        jobs[j.connector_id].append(j)
    key_cid = {j.key: j.connector_id for lst in jobs.values() for j in lst}
    runs: dict[int, dict] = defaultdict(lambda: {"runs": 0, "ok": 0, "error": 0, "lost": 0, "running": 0, **{k: 0 for k in COUNTS}})
    outcomes: dict[str, int] = defaultdict(int)  # P1: complete / partial / rate_limited / schema_changed / auth_required / blocked / failed / cancelled
    for r in db.scalars(select(CrawlRun).where(CrawlRun.started_at >= since)):
        if r.outcome:
            outcomes[r.outcome] += 1
        agg = runs[key_cid.get(r.job_key)]
        agg["runs"] += 1
        agg[r.status if r.status in ("ok", "error", "lost", "running") else "error"] += 1
        for k in COUNTS:
            agg[k] += getattr(r, k) or 0
    live: dict[str, dict] = defaultdict(lambda: {"jobs": 0, "fetched": 0, "new": 0, "failed": 0})
    # ponytail: Python-side sum over the window's search jobs (hundreds a day); a per-source rollup table if it grows
    for (sources,) in db.execute(select(SearchJob.sources).where(SearchJob.created_at >= since, SearchJob.sources.is_not(None))):
        for name, s in (sources or {}).items():
            if isinstance(s, dict):
                agg = live[name]
                agg["jobs"] += 1
                for k in ("fetched", "new", "failed"):
                    agg[k] += int(s.get(k) or 0)
    out = []
    for c in conns:
        js = jobs.get(c.id, [])
        out.append({
            "id": c.id, "name": c.name, "adapter": c.adapter, "network": connector_network(c.adapter, c.config), "enabled": c.enabled,
            "health": c.health, "status": c.status, "last_error": c.last_error, "last_sync_at": c.last_sync_at,
            "scheduled": dict(runs[c.id]), "live_search": dict(live[c.name]) if c.name in live else None,
            "jobs": [{"key": j.key, "status": j.status, "retry_count": j.retry_count, "next_retry_at": j.next_retry_at,
                      "lease_owner": j.lease_owner, "heartbeat_at": j.heartbeat_at, "last_error": j.last_error,
                      "cursor": cursor_summary(j.checkpoint)} for j in sorted(js, key=lambda j: j.key)],
            "retry_wait": sum(1 for j in js if j.status == "retry_wait"), "blocked_jobs": sum(1 for j in js if j.status == "blocked"),
        })
    media: dict[str, dict] = defaultdict(lambda: {"total": 0, "thumb": 0})
    for net, status, n, thumbs in db.execute(select(Creative.network, Creative.status, func.count(), func.count(Creative.thumb_key))
                                             .group_by(Creative.network, Creative.status)):
        m = media[net or "unknown"]
        m[status] = m.get(status, 0) + n
        m["total"] += n
        m["thumb"] += thumbs
    for m in media.values():  # can the card show something: a stored file or at least the saved cover
        m["available_pct"] = round(100 * max(m.get("stored", 0), m["thumb"]) / m["total"], 1) if m["total"] else None
    totals = {k: sum(r["scheduled"][k] for r in out) for k in ("runs", "ok", "error", "lost", *COUNTS)}
    totals["live_fetched"] = sum((r["live_search"] or {}).get("fetched", 0) for r in out)
    totals["unhealthy"] = sum(1 for r in out if r["enabled"] and r["health"] not in (None, "healthy", "unknown", "waiting_file", "configured"))
    totals["retry_wait"] = sum(r["retry_wait"] for r in out)
    totals["blocked_jobs"] = sum(r["blocked_jobs"] for r in out)
    totals["outcomes"] = dict(outcomes)
    return {"window_hours": hours, "generated_at": now, "totals": totals, "connectors": out, "media": dict(media)}


if __name__ == "__main__":  # operators: the Data & Connectors system area is service-token only (no browser access)
    import json
    import sys

    from ..db import SessionLocal

    with SessionLocal() as db:
        print(json.dumps(collector_kpis(db, int(sys.argv[1]) if len(sys.argv) > 1 else 24), default=str, ensure_ascii=False, indent=1))
