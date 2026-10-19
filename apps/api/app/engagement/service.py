"""Engagement resolution for one ad: what the source already told us → the public post it maps to → (opt-in, budgeted) Bright Data.

Order, cheapest first, and it stops where the data stops:
  1. ad scope   the numbers the ad's own source reported (ads.likes / comments_count / shares / views; NULL = never reported). Free.
  2. mapping    Ad ID → Post ID / Video ID only when the ad names a public post (mapping.py). No link = engagement stays N/A.
  3. memory     a fresh `post` observation of that same post (any ad of the page) — no request.
  4. provider   Bright Data Facebook / TikTok Posts, one record, only on an explicit lookup or when ENGAGEMENT_AUTO_ENABLED is on, only
                inside brightdata_posts' own budget. A disabled / out-of-budget provider is recorded (short TTL) — never turned into zeros.
Every look is a row in ad_engagement (history); the card reads summary().
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import SessionLocal
from ..models import Ad
from ..pricing.quota import env_int
from ..visual.lens import _lock
from . import brightdata_posts as bd
from . import mapping, matching
from .models import AD, POST, AdEngagement, PageFetch, PagePost

log = logging.getLogger("app.engagement")
TTL_H = {"ok": 24, "not_found": 24, "unmapped": 24 * 7, "needs_provider": 6, "disabled": 1, "quota": 6, "error": 0.5}
METRICS = ("likes", "comments", "shares", "views")


def ttl_hours(status: str) -> float:
    return float(env_int("ENGAGEMENT_TTL_HOURS", 24)) if status == "ok" else TTL_H.get(status, 6)


def _row(ad_id: int, scope: str, provider: str, status: str, reason: str | None = None, *, ref: dict | None = None, vals: dict | None = None,
         evidence: dict | None = None, observed_at: datetime | None = None) -> AdEngagement:
    now = observed_at or datetime.utcnow()
    ref, vals = ref or {}, vals or {}
    return AdEngagement(ad_id=ad_id, scope=scope, provider=provider, status=status, reason=reason, observed_at=now,
                        expires_at=datetime.utcnow() + timedelta(hours=ttl_hours(status)), platform=ref.get("platform"),
                        post_id=vals.get("post_id") or ref.get("post_id"), post_url=ref.get("url"),
                        likes=vals.get("likes"), comments=vals.get("comments"), shares=vals.get("shares"), views=vals.get("views"),
                        evidence={**(evidence or {}), **({"mapped_via": ref["via"]} if ref.get("via") else {})})


def record_ad_scope(db: Session, ad: Ad, source: str | None = None) -> AdEngagement | None:
    """History row for the ad's own numbers when they differ from the last row (called by ingest on change, and by the backfill)."""
    cur = {"likes": ad.likes, "comments": ad.comments_count, "shares": ad.shares, "views": ad.views}
    if all(v is None for v in cur.values()):
        return None
    last = db.scalar(select(AdEngagement).where(AdEngagement.ad_id == ad.id, AdEngagement.scope == AD, AdEngagement.status == "ok")
                     .order_by(AdEngagement.observed_at.desc(), AdEngagement.id.desc()).limit(1))
    if last and (last.likes, last.comments, last.shares, last.views) == tuple(cur.values()):
        return None
    row = _row(ad.id, AD, source or ad.source or "source", "ok", vals=cur, evidence={"from": "source"})
    row.expires_at = None  # the ad's own numbers do not expire; a newer row supersedes
    db.add(row)
    return row


def _fresh_post(db: Session, ad_id: int, ref: dict) -> AdEngagement | None:
    now = datetime.utcnow()
    q = select(AdEngagement).where(AdEngagement.scope == POST, AdEngagement.status == "ok", AdEngagement.expires_at > now)
    if ref.get("post_id"):
        q = q.where(AdEngagement.platform == ref["platform"], AdEngagement.post_id == ref["post_id"])
    else:
        q = q.where(AdEngagement.ad_id == ad_id, AdEngagement.post_url == ref["url"])
    return db.scalar(q.order_by(AdEngagement.observed_at.desc()).limit(1))


def resolve(db: Session, ad_id: int, allow_provider: bool = False) -> dict:
    r = _resolve(db, ad_id, allow_provider)
    db.flush()   # these sessions run with autoflush off: summary() must see the rows just added
    return r


def _resolve(db: Session, ad_id: int, allow_provider: bool) -> dict:
    """One ad. Writes observation rows on `db` (caller commits). Returns {status, reason, steps, row?}."""
    ad = db.get(Ad, ad_id)
    if not ad:
        raise ValueError("ad not found")
    steps: list[dict] = []
    record_ad_scope(db, ad)
    steps.append({"step": "ad_scope", "ok": any(v is not None for v in (ad.likes, ad.comments_count, ad.shares, ad.views))})
    ref = mapping.post_ref(ad)
    if "reason" in ref and ad.page_id and ref["reason"] in ("ad_library_has_no_post_id", "no_post_link") and (ad.network in (None, "meta") or ad.platform in ("facebook", "instagram")):
        got = _via_page(db, ad, allow_provider, steps)  # no post id on the ad: find it among the page's posts by the project's identity signals
        if got:
            return got
    if "reason" in ref:
        steps.append({"step": "mapping", "ok": False, "code": ref["reason"]})
        last = db.scalar(select(AdEngagement).where(AdEngagement.ad_id == ad_id, AdEngagement.scope == POST, AdEngagement.status == "unmapped")
                         .order_by(AdEngagement.observed_at.desc()).limit(1))
        if not last or last.reason != ref["reason"] or (last.expires_at and last.expires_at < datetime.utcnow()):
            db.add(_row(ad_id, POST, "mapping", "unmapped", ref["reason"]))
        return {"status": "unmapped", "reason": ref["reason"], "steps": steps}
    steps.append({"step": "mapping", "ok": True, "code": ref["platform"], "post_id": ref.get("post_id"), "via": ref["via"]})
    hit = _fresh_post(db, ad_id, ref)
    if hit:
        if hit.ad_id != ad_id:  # same post, another ad of the page: reuse the numbers, no request
            db.add(_row(ad_id, POST, hit.provider, "ok", ref=ref, vals={"likes": hit.likes, "comments": hit.comments, "shares": hit.shares,
                                                                          "views": hit.views, "post_id": hit.post_id}, evidence={"copied_from": hit.id}))
        steps.append({"step": "memory", "ok": True})
        return {"status": "ok", "reason": None, "steps": steps}
    if not allow_provider:
        steps.append({"step": "provider", "ok": False, "code": "needs_click"})
        last = db.scalar(select(AdEngagement).where(AdEngagement.ad_id == ad_id, AdEngagement.scope == POST, AdEngagement.status == "needs_provider")
                         .order_by(AdEngagement.observed_at.desc()).limit(1))
        if not last or (last.expires_at and last.expires_at < datetime.utcnow()):  # the card learns "a post exists, one click fetches it"
            db.add(_row(ad_id, POST, "mapping", "needs_provider", "needs_click", ref=ref))
        return {"status": "needs_provider", "reason": "needs_click", "steps": steps}
    key = f"{ref['platform']}:{ref.get('post_id') or ref['url']}"
    db.commit()  # release the writer before the network + budget ledger (another session) — SQLite would wait on us otherwise
    with _lock(f"engagement:{key}"):
        with SessionLocal() as chk:  # own short session: ours stays closed while the ledger / provider run
            hit = _fresh_post(chk, ad_id, ref)  # another worker may have fetched it while we waited for the lock
        if hit:
            steps.append({"step": "memory", "ok": True})
            return {"status": "ok", "reason": None, "steps": steps}
        why = bd.unavailable()
        if why:
            db.add(_row(ad_id, POST, "brightdata_posts", "disabled", why, ref=ref))
            steps.append({"step": "brightdata_posts", "ok": False, "code": why})
            return {"status": "disabled", "reason": why, "steps": steps}
        try:
            rec = bd.fetch(ref["platform"], ref["url"])
        except bd.PostsError as e:
            status = "quota" if e.code.startswith("quota_exceeded") else "not_found" if e.code == "not_found" else "error"
            db.add(_row(ad_id, POST, "brightdata_posts", status, e.code, ref=ref))
            steps.append({"step": "brightdata_posts", "ok": False, "code": e.code})
            return {"status": status, "reason": e.code, "steps": steps}
        vals = bd.parse(ref["platform"], rec)
        got = any(vals[m] is not None for m in METRICS)
        db.add(_row(ad_id, POST, "brightdata_posts", "ok" if got else "not_found", None if got else "no_metrics_in_record", ref=ref, vals=vals,
                    evidence={"dataset": bd.DATASETS[ref["platform"]], "fields": vals["fields"], "record_keys": sorted(rec)[:40]}))
        steps.append({"step": "brightdata_posts", "ok": got, "code": "ok" if got else "no_metrics_in_record", "post_id": vals["post_id"]})
        return {"status": "ok" if got else "not_found", "reason": None if got else "no_metrics_in_record", "steps": steps}


def _num(v):
    return None if v is None else int(v)


def summary(db: Session, ad_ids: list[int]) -> dict[int, dict]:
    """What the card shows per ad: ad-scope numbers (from the ad row), post-scope numbers (latest ok observation, own label), and why a
    field is N/A. Fields are None when unknown, never 0."""
    from .models import EngagementJob

    out: dict[int, dict] = {}
    if not ad_ids:
        return out
    db.flush()
    ads = {a.id: a for a in db.scalars(select(Ad).where(Ad.id.in_(ad_ids)))}
    obs = db.scalars(select(AdEngagement).where(AdEngagement.ad_id.in_(ad_ids)).order_by(AdEngagement.observed_at.desc(), AdEngagement.id.desc())).all()
    jobs = {j.ad_id: j for j in db.scalars(select(EngagementJob).where(EngagementJob.ad_id.in_(ad_ids)))}
    by_ad: dict[int, list[AdEngagement]] = {}
    for o in obs:
        by_ad.setdefault(o.ad_id, []).append(o)
    now = datetime.utcnow()
    for aid in ad_ids:
        a = ads.get(aid)
        if a is None:
            continue
        rows = by_ad.get(aid, [])
        ad_vals = {"likes": _num(a.likes), "comments": _num(a.comments_count), "shares": _num(a.shares), "views": _num(a.views)}
        ad_last = next((o for o in rows if o.scope == AD and o.status == "ok"), None)
        post_ok = next((o for o in rows if o.scope == POST and o.status == "ok"), None)
        post_any = next((o for o in rows if o.scope == POST), None)
        job = jobs.get(aid)
        status = "ok" if post_ok else (post_any.status if post_any else ("queued" if job and job.status in ("queued", "running") else "unknown"))
        out[aid] = {
            "ad_id": aid,
            "ad": {**ad_vals, "provider": a.source, "observed_at": ad_last.observed_at if ad_last else None} if any(v is not None for v in ad_vals.values()) else None,
            "post": {"likes": post_ok.likes, "comments": post_ok.comments, "shares": post_ok.shares, "views": post_ok.views, "provider": post_ok.provider,
                     "platform": post_ok.platform, "post_id": post_ok.post_id, "url": post_ok.post_url, "observed_at": post_ok.observed_at,
                     "stale": bool(post_ok.expires_at and post_ok.expires_at < now)} if post_ok else None,
            "status": status, "reason": post_any.reason if post_any and not post_ok else None,
            "job": job.status if job else None,
        }
    return out


# ------------------------------------------------------------ ad → post through the page's own posts (matching.py)
def page_url_of(db: Session, ad: Ad) -> str:
    from ..models import Advertiser

    adv = db.get(Advertiser, ad.advertiser_id) if ad.advertiser_id else None
    return (adv.page_url if adv and adv.page_url and "facebook.com" in adv.page_url else f"https://www.facebook.com/{ad.page_id}")


def page_ttl() -> timedelta:
    return timedelta(days=env_int("ENGAGEMENT_PAGE_TTL_DAYS", 7))


def apply_matches(db: Session, page_key: str) -> dict:
    """Match every ad of the page against its stored posts and write one observation per ad (ok with the post's numbers, or unmapped with
    the reason). No request: the numbers are the stored post's."""
    posts = list(db.scalars(select(PagePost).where(PagePost.page_key == page_key)))
    out = {"ads": 0, "matched": 0}
    for ad in db.scalars(select(Ad).where(Ad.page_id == page_key)):
        post, score, fams, reason = matching.best_post(db, ad, posts)
        out["ads"] += 1
        if post is not None:
            vals = {"likes": post.likes, "comments": post.comments, "shares": post.shares, "views": post.views, "post_id": post.post_id}
            ref = {"platform": "facebook", "post_id": post.post_id, "url": post.url, "via": "page_match"}
            db.add(_row(ad.id, POST, "brightdata_posts", "ok", ref=ref, vals=vals,
                        evidence={"matched_by": fams, "score": score, "page_key": page_key, "posts_considered": len(posts), "fetched_at": post.fetched_at.isoformat()}))
            out["matched"] += 1
        else:
            db.add(_row(ad.id, POST, "mapping", "unmapped", reason, evidence={"best_score": score, "matched_by": fams, "posts_considered": len(posts)}))
    db.flush()
    return out


def _via_page(db: Session, ad: Ad, allow_provider: bool, steps: list) -> dict | None:
    """Returns a result dict when the page route decided something, None to fall back to the plain 'unmapped' answer."""
    key = ad.page_id
    fetch = db.scalar(select(PageFetch).where(PageFetch.page_key == key))
    now = datetime.utcnow()
    fresh = bool(fetch and fetch.status in ("ready", "empty") and fetch.fetched_at and now - fetch.fetched_at < page_ttl())
    if fetch and fetch.status == "pending":
        steps.append({"step": "page_posts", "ok": False, "code": "page_posts_pending", "snapshot": fetch.snapshot_id})
        return {"status": "pending", "reason": "page_posts_pending", "steps": steps}
    if not fresh:
        if not allow_provider:
            steps.append({"step": "page_posts", "ok": False, "code": "needs_click"})
            last = db.scalar(select(AdEngagement).where(AdEngagement.ad_id == ad.id, AdEngagement.scope == POST, AdEngagement.status == "needs_provider")
                             .order_by(AdEngagement.observed_at.desc()).limit(1))
            if not last or (last.expires_at and last.expires_at < now):
                db.add(_row(ad.id, POST, "mapping", "needs_provider", "page_posts_not_fetched", evidence={"page_key": key}))
            return {"status": "needs_provider", "reason": "page_posts_not_fetched", "steps": steps}
        r = fetch_page(db, ad)
        steps.extend(r["steps"])
        if r["status"] != "ok":
            return r
    m = apply_matches(db, key)
    got = db.scalar(select(AdEngagement).where(AdEngagement.ad_id == ad.id, AdEngagement.scope == POST).order_by(AdEngagement.id.desc()).limit(1))
    steps.append({"step": "page_match", "ok": bool(got and got.status == "ok"), "code": (got.reason if got and got.status != "ok" else "matched"), **m})
    return {"status": got.status if got else "unmapped", "reason": got.reason if got else "no_posts", "steps": steps}


def fetch_page(db: Session, ad: Ad) -> dict:
    """One Bright Data request for the page's recent posts (single-flight per page). Stores the posts, or the pending snapshot."""
    key, url = ad.page_id, page_url_of(db, ad)
    n = env_int("ENGAGEMENT_PAGE_POSTS", 20)
    db.commit()  # release the writer before the network + ledger (another session)
    with _lock(f"engagement-page:{key}"):
        with SessionLocal() as chk:
            f = chk.scalar(select(PageFetch).where(PageFetch.page_key == key))
            if f and f.status in ("ready", "empty") and f.fetched_at and datetime.utcnow() - f.fetched_at < page_ttl():
                return {"status": "ok", "steps": [{"step": "page_posts", "ok": True, "code": "memory"}]}
        why = bd.unavailable()
        if why:
            db.add(_row(ad.id, POST, "brightdata_posts", "disabled", why, evidence={"page_key": key}))
            return {"status": "disabled", "reason": why, "steps": [{"step": "page_posts", "ok": False, "code": why}]}
        requested = datetime.utcnow()
        try:  # no query on `db` between here and the answer: its transaction would hold SQLite's writer while the ledger (own session) writes
            recs = bd.submit("facebook_page", [{"url": url, "num_of_posts": n}])
        except bd.PostsPending as e:
            row = db.scalar(select(PageFetch).where(PageFetch.page_key == key)) or PageFetch(page_key=key, page_url=url, status="pending")
            row.page_url, row.requested_at = url, requested
            row.status, row.snapshot_id = "pending", e.snapshot_id
            db.add(row)
            return {"status": "pending", "reason": "page_posts_pending", "steps": [{"step": "page_posts", "ok": False, "code": "page_posts_pending", "snapshot": e.snapshot_id}]}
        except bd.PostsError as e:
            status = "quota" if e.code.startswith("quota_exceeded") else "error"
            row = db.scalar(select(PageFetch).where(PageFetch.page_key == key)) or PageFetch(page_key=key, page_url=url, status="failed")
            row.page_url, row.requested_at = url, requested
            row.status, row.error = "failed", e.code
            db.add(row)
            db.add(_row(ad.id, POST, "brightdata_posts", status, e.code, evidence={"page_key": key}))
            return {"status": status, "reason": e.code, "steps": [{"step": "page_posts", "ok": False, "code": e.code}]}
        row = db.scalar(select(PageFetch).where(PageFetch.page_key == key)) or PageFetch(page_key=key, page_url=url, status="pending")
        row.page_url, row.requested_at = url, requested
        parsed = [bd.parse_page_post(r) for r in recs]
        total = matching.store_posts(db, key, parsed)
        row.status, row.snapshot_id, row.posts, row.error, row.fetched_at = ("ready" if total else "empty"), None, total, None, datetime.utcnow()
        db.add(row)
        db.flush()
        return {"status": "ok", "steps": [{"step": "page_posts", "ok": bool(total), "code": "ok" if total else "empty", "posts": total}]}


def collect_pending(db: Session) -> dict:
    """Worker, free: poll page snapshots that were still running, store their posts and match the page's ads."""
    done = 0
    for f in list(db.scalars(select(PageFetch).where(PageFetch.status == "pending", PageFetch.snapshot_id.is_not(None)))):
        try:
            st, recs = bd.collect(f.snapshot_id)
        except Exception as e:  # noqa: BLE001 - network blip: next tick
            log.warning("collect %s: %s", f.snapshot_id, str(e)[:100])
            continue
        if st == "running":
            if datetime.utcnow() - f.requested_at > timedelta(hours=2):
                f.status, f.error = "failed", "snapshot_timeout"
            continue
        if st == "failed":
            f.status, f.error = "failed", "snapshot_failed"
            continue
        total = matching.store_posts(db, f.page_key, [bd.parse_page_post(r) for r in recs])
        f.status, f.posts, f.fetched_at, f.error = ("ready" if total else "empty"), total, datetime.utcnow(), None
        apply_matches(db, f.page_key)
        done += 1
    db.flush()
    return {"collected": done}
