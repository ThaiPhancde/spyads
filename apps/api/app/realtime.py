"""Hybrid realtime (realtime_product_fit_summary §2-§7, §11-§14, §21).

Tier 0  (< 5 s)      webhooks: orders, shipments, comments → incremental re-score → alerts → SSE
Tier 1  (1-5 min)    own ad accounts (connector every_minutes)
Tier 2  (15-60 min)  spy sources: tracked keyword / competitor-page queries
Tier 3  (6-24 h)     enrichment + daily snapshot
One asyncio scheduler inside the API process (fine for a single instance). For several
instances move jobs to Redis + Celery/RQ and keep this module as the job body.
"""
from __future__ import annotations

import asyncio
import logging
import queue
import re
import threading
import time
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from .collector.base import AuthExpired, ConnectorError, FetchParams, NotConfigured
from .collector.factory import ConnectorFactory
from .db import SessionLocal
from .events import publish
from .ingest import ingest_ads
from .models import Alert, Connector, Order, Product, SearchJob, TrackedQuery

log = logging.getLogger(__name__)
# ponytail: global ingest lock, per-source locks if throughput matters — two live searches (or one + the scheduler)
# writing at once cost one of them its whole Meta batch ("database is locked") while the job still said "done"
_ingest_lock = threading.Lock()


def _redact(s: str | None) -> str | None:
    """Source errors echo request URLs — never the Apify token in them."""
    return re.sub(r"(token=)[^&\s'\"]+", r"\1***", s) if s else s

# ------------------------------------------------------------ carrier status normalization (§12, §14)
STAGE_MAP = {
    "NEW": ["new", "created", "pending", "chờ xác nhận", "moi", "mới"],
    "CONTACTING": ["contacting", "calling", "đang gọi", "callback", "no_answer_retry"],
    "CONFIRMED": ["confirmed", "đã xác nhận", "xac_nhan", "approved"],
    "PACKED": ["packed", "đóng gói", "ready_to_ship", "picking"],
    "SHIPPED": ["shipped", "picked_up", "handed_over", "đã lấy hàng", "dispatched"],
    "IN_TRANSIT": ["in_transit", "transit", "đang vận chuyển", "on_the_way", "arrived_at_hub"],
    "OUT_FOR_DELIVERY": ["out_for_delivery", "đang giao", "delivering", "ofd"],
    "DELIVERED": ["delivered", "đã giao", "giao thành công", "success", "completed", "cod_collected"],
    "REFUSED": ["refused", "customer_refused", "refused_by_consignee", "rto_customer_reject", "rejected", "từ chối",
                "khách từ chối", "reject_by_customer", "customer_rejected"],
    "UNREACHABLE": ["unreachable", "customer_unreachable", "no_answer", "không liên lạc được", "phone_off", "khách không nghe máy"],
    "FAILED": ["failed", "delivery_failed", "giao thất bại", "undelivered", "attempt_failed", "wrong_address", "sai địa chỉ"],
    "RETURNED": ["returned", "rto", "return_to_origin", "đã hoàn", "hoàn hàng", "rts", "return_delivered"],
    "CANCELLED": ["cancelled", "canceled", "hủy", "huỷ", "void"],
    "DUPLICATE": ["duplicate", "trùng đơn", "dup"],
    "FAKE_ORDER": ["fake", "fake_order", "spam", "đơn ảo", "đơn rác"],
    "REFUNDED": ["refunded", "hoàn tiền"],
}
_LOOKUP = {v: k for k, vs in STAGE_MAP.items() for v in vs}
STAGE_TO_STATUS = {"NEW": "pending", "CONTACTING": "pending", "CONFIRMED": "confirmed", "PACKED": "confirmed",
                   "SHIPPED": "shipped", "IN_TRANSIT": "shipped", "OUT_FOR_DELIVERY": "shipped", "DELIVERED": "delivered",
                   "REFUSED": "refused", "UNREACHABLE": "failed", "FAILED": "failed", "RETURNED": "returned",
                   "CANCELLED": "cancelled", "DUPLICATE": "cancelled", "FAKE_ORDER": "cancelled", "REFUNDED": "returned"}
FAILURE_REASON = {"REFUSED": "CUSTOMER_REFUSED", "UNREACHABLE": "CUSTOMER_UNREACHABLE", "CANCELLED": "CUSTOMER_CANCELLED",
                  "DUPLICATE": "DUPLICATE_ORDER", "FAKE_ORDER": "FAKE_ORDER"}
STAGE_EVENT = {"NEW": "ORDER_CREATED", "CONFIRMED": "ORDER_CONFIRMED", "CANCELLED": "ORDER_CANCELLED", "SHIPPED": "SHIPMENT_CREATED",
               "DELIVERED": "SHIPMENT_DELIVERED", "FAILED": "SHIPMENT_FAILED", "UNREACHABLE": "SHIPMENT_FAILED",
               "REFUSED": "SHIPMENT_REFUSED", "RETURNED": "SHIPMENT_RETURNED"}


def normalize_stage(raw: str | None) -> str | None:
    if not raw:
        return None
    k = raw.strip().lower().replace("-", "_")
    if k.upper() in STAGE_MAP:
        return k.upper()
    if k in _LOOKUP:
        return _LOOKUP[k]
    for v, stage in _LOOKUP.items():
        if v in k:
            return stage
    return None


def apply_order_event(db: Session, rec: dict) -> tuple[Order | None, str | None]:
    """Upsert one order / shipment update. Returns (order, event_type)."""
    from .services.connectors import _product_by_ref, _f

    o = None
    if rec.get("external_id"):
        o = db.scalar(select(Order).where(Order.external_id == str(rec["external_id"])))
    if o is None and rec.get("tracking_code"):
        o = db.scalar(select(Order).where(Order.tracking_code == str(rec["tracking_code"])))
    raw_status = rec.get("carrier_status") or rec.get("status") or rec.get("stage")
    stage = normalize_stage(raw_status) or ("NEW" if o is None else None)
    created = False
    if o is None:
        product = _product_by_ref(db, rec)
        if not product:
            raise ValueError("order needs product_code / product_id / product_name")
        o = Order(external_id=str(rec.get("external_id")) if rec.get("external_id") else None, product_id=product.id,
                  source=rec.get("source") or "crm", created_at=datetime.utcnow())
        db.add(o)
        created = True
    for k in ("country", "carrier", "tracking_code", "campaign_id", "adset_id", "ad_external_id", "creative_ref",
              "lead_id", "conversation_id", "sales_agent"):
        if rec.get(k):
            setattr(o, k, str(rec[k]))
    for k in ("amount", "cogs", "shipping_cost", "cod_fee", "sales_commission", "return_cost", "payment_fee"):
        if rec.get(k) not in (None, ""):
            setattr(o, k, _f(rec[k], 0))
    if rec.get("experiment_id"):
        o.experiment_id = int(rec["experiment_id"])
    if rec.get("refusal_note"):
        o.refusal_reason_raw = rec["refusal_note"]
    if raw_status:
        o.carrier_status_raw = str(raw_status)[:128]
    event = None
    if stage and stage != o.stage:
        o.stage = stage
        o.status = STAGE_TO_STATUS.get(stage, o.status)
        o.refunded = stage in ("REFUNDED",) or o.refunded
        o.failure_reason = rec.get("failure_reason") or FAILURE_REASON.get(stage) or (
            "WRONG_ADDRESS" if "address" in str(raw_status).lower() or "địa chỉ" in str(raw_status).lower() else o.failure_reason)
        event = STAGE_EVENT.get(stage)
    if created and not event:
        event = "ORDER_CREATED"
    o.updated_at = datetime.utcnow()
    return o, event


# ------------------------------------------------------------ incremental alerts per product (§6, §21)
def check_product_realtime(db: Session, pid: int):
    """Refusal / delivery / sentiment spike check on the latest closed orders of one product."""
    orders = db.scalars(select(Order).where(Order.product_id == pid).order_by(Order.updated_at.desc().nullslast(), Order.id.desc()).limit(400)).all()
    closed = [o for o in orders if o.status in ("delivered", "refused", "failed", "returned")]
    if len(closed) < 40:
        return
    recent, prev = closed[:30], closed[30:130]
    rate = lambda lst, st: sum(1 for o in lst if o.status in st) / len(lst) if lst else None
    p = db.get(Product, pid)
    r_now, r_prev = rate(recent, {"refused"}), rate(prev, {"refused"})
    if r_prev is not None and r_now - r_prev >= 0.07:
        _alert(db, "cod_refusal_increasing", "critical", f"⚠ COD refusal tăng bất thường: {p.canonical_name}",
               f"Refusal {r_prev:.0%} → {r_now:.0%} (30 đơn đóng gần nhất).", pid)
        publish("REFUSAL_SPIKE", {"from": r_prev, "to": r_now}, product_id=pid, db=db)
    d_now, d_prev = rate(recent, {"delivered", "returned"}), rate(prev, {"delivered", "returned"})
    if d_prev is not None and d_prev - d_now >= 0.08:
        _alert(db, "delivery_rate_dropping", "critical", f"📉 Delivery rate giảm: {p.canonical_name}",
               f"Delivery {d_prev:.0%} → {d_now:.0%}.", pid)
        publish("DELIVERY_RATE_DROP", {"from": d_prev, "to": d_now}, product_id=pid, db=db)


def _alert(db: Session, type_: str, sev: str, title: str, msg: str, pid: int | None = None, data: dict | None = None):
    key = f"{type_}:{pid}:{datetime.utcnow():%Y%m%d%H}"
    if db.scalar(select(Alert.id).where(Alert.dedupe_key == key)):
        return
    db.add(Alert(type=type_, severity=sev, title=title, message=msg, product_id=pid, data=data or {}, dedupe_key=key))
    publish("ALERT", {"type": type_, "severity": sev, "title": title, "message": msg}, product_id=pid, persist=False)


def after_business_events(db: Session, product_ids: set[int]):
    from .services.engine import rescore_products

    rescore_products(db, product_ids)
    for pid in product_ids:
        check_product_realtime(db, pid)
    db.commit()


# ------------------------------------------------------------ connector / query jobs
def _params_from(c: Connector, q: TrackedQuery | None = None) -> FetchParams:
    cfg = c.config or {}
    countries = (q.countries if q and q.countries else None) or \
        [x.strip().upper() for x in str(cfg.get("countries") or "ALL").split(",") if x.strip()]
    return FetchParams(query=q.query if q else None, countries=countries or ["ALL"],
                       page_ids=(q.page_ids if q else []) or [], media_type=q.media_type if q else None,
                       limit=(q.limit if q else int(cfg.get("max_per_query") or 60)))


def run_connector(cid: int, query: TrackedQuery | None = None, params: FetchParams | None = None) -> dict:
    """Fetch → ingest → incremental score. Runs in a worker thread."""
    from .services.engine import rescore_products

    t0 = time.perf_counter()
    with SessionLocal() as db:
        c = db.get(Connector, cid)
        if not c or not c.adapter:
            return {"error": "connector has no adapter"}
        result = {"connector": c.name, "fetched": 0}
        db.commit()  # close the read transaction before calling the source over the network
        try:
            connector = ConnectorFactory.get(c.adapter, c.config or {})  # KeyError = adapter removed from the factory
            if c.adapter == "meta_ads":
                rows = connector.fetch_metrics("today")
                pids = upsert_ad_metrics(db, rows, c.config or {})
                rescore_products(db, pids)
                c.status, c.health, c.last_error = "ok", "healthy", None
                c.last_sync_at, c.last_sync_count = datetime.utcnow(), len(rows)
                c.last_duration_ms = int((time.perf_counter() - t0) * 1000)
                db.commit()
                result.update(fetched=len(rows), products=len(pids))
                publish("CONNECTOR_SYNCED", result)
                return result
            plist = [params] if params else ([_params_from(c, query)] if query else _connector_param_list(c))
            if not plist:  # keyword-search source without scheduled keywords: it runs from live searches only
                c.status, c.last_error = "ok", None
                db.commit()
                return {**result, "skipped": "chỉ chạy khi tìm từ khoá (Tìm sản phẩm) hoặc khi cấu hình keywords"}
            recs = []
            for p in plist:
                for r in connector.fetch_ads(p):
                    r.matched_query = p.query
                    recs.append(r)
            with _ingest_lock:
                stats = ingest_ads(db, recs)
                rescore_products(db, stats["product_ids"])
                c = db.get(Connector, cid)
                c.status, c.health, c.last_error = "ok", "healthy", None
                if not recs and c.adapter != "export":  # an empty watch folder is normal; blueprint §7: an empty result never means "no ads" — flag it, keep previous data untouched
                    c.health = "degraded"
                    c.last_error = "Nguồn trả về 0 kết quả — có thể bị chặn / đổi API; dữ liệu cũ được giữ nguyên"
                if stats["errors"] and not (stats["new_ads"] or stats["updated_ads"]):  # fetched fine, stored nothing
                    c.health, c.last_error = "error", _redact(stats["errors"][0])[:500]
                c.last_sync_at, c.last_sync_count = datetime.utcnow(), len(recs)
                c.last_duration_ms = int((time.perf_counter() - t0) * 1000)
                if c.last_duration_ms > 120_000:
                    c.health = "slow"
                if query:
                    q = db.get(TrackedQuery, query.id)
                    q.last_run_at, q.last_count, q.last_new, q.last_error = datetime.utcnow(), len(recs), stats["new_ads"], None
                db.commit()
            result.update(fetched=len(recs), **{k: stats[k] for k in ("new_ads", "updated_ads", "new_products", "creatives_queued", "merged_cross_source")})
            publish("CONNECTOR_SYNCED", result)
        except (NotConfigured, AuthExpired, ConnectorError, Exception) as e:
            if isinstance(e, OperationalError):
                log.exception("connector %s: batch lost (sqlite busy)", cid)
            db.rollback()
            for _try in range(3):  # the failure may itself be "database is locked": retry the status write briefly
                try:
                    c = db.get(Connector, cid)
                    break
                except OperationalError:
                    db.rollback()
                    time.sleep(2)
            c.status = "not_configured" if isinstance(e, NotConfigured) else "error"
            c.health = "auth_expired" if isinstance(e, AuthExpired) else ("unknown" if isinstance(e, NotConfigured) else "error")
            c.last_error = "adapter removed" if isinstance(e, KeyError) else _redact(f"{type(e).__name__}: {e}")[:500]
            c.last_sync_at = datetime.utcnow()
            if query:
                q = db.get(TrackedQuery, query.id)
                q.last_run_at, q.last_error = datetime.utcnow(), c.last_error
            try:
                db.commit()
            except OperationalError:
                log.exception("connector %s: could not even record the failure (sqlite busy)", cid)
                db.rollback()
            result["error"] = c.last_error
            publish("CONNECTOR_FAILED", result)
        return result


def upsert_ad_metrics(db: Session, rows: list[dict], cfg: dict) -> set[int]:
    """Own ad metrics → ad_metrics (idempotent per ad/day) + product mapping by code or keyword map."""
    import json
    import re

    from .models import AdMetric

    pmap = cfg.get("product_map") or {}
    if isinstance(pmap, str):
        pmap = json.loads(pmap) if pmap.strip() else {}
    codes = {p.product_code: p.id for p in db.scalars(select(Product))}
    pids: set[int] = set()
    for r in rows:
        d = datetime.fromisoformat(str(r["date"])[:10]).date()
        m = db.scalar(select(AdMetric).where(AdMetric.ad_id == str(r["ad_id"]), AdMetric.date == d))
        if not m:
            m = AdMetric(ad_id=str(r["ad_id"]), date=d)
            db.add(m)
        for k in ("platform", "account_id", "campaign_id", "campaign_name", "adset_id", "ad_name", "spend", "impressions",
                  "clicks", "leads", "purchases", "currency"):
            if r.get(k) is not None:
                setattr(m, k, r[k])
        m.updated_at = datetime.utcnow()
        names = f"{r.get('campaign_name') or ''} {r.get('ad_name') or ''}"
        code = re.search(r"PRD_\d{7}", names)
        pid = codes.get(code.group(0)) if code else None
        if pid is None:
            for kw, pc in pmap.items():
                if kw.lower() in names.lower():
                    pid = codes.get(pc)
                    break
        if r.get("product_code"):
            pid = codes.get(r["product_code"], pid)
        m.product_id = pid or m.product_id
        if m.product_id:
            pids.add(m.product_id)
    db.flush()
    return pids


def _connector_param_list(c: Connector) -> list[FetchParams]:
    """Scheduled sync without a tracked query: config keywords × countries, or page ids."""
    cfg = c.config or {}
    base = _params_from(c)
    kws = [k.strip() for k in str(cfg.get("keywords") or "").replace(",", "\n").splitlines() if k.strip()]
    pages = [p.strip() for p in str(cfg.get("page_ids") or "").split(",") if p.strip()]
    out = [FetchParams(query=k, countries=base.countries, limit=base.limit) for k in kws]
    if pages:
        out.append(FetchParams(page_ids=pages, countries=base.countries, limit=base.limit))
    # search-type sources need a keyword / page list — never pull random ads
    cls = ConnectorFactory.connectors.get(c.adapter)
    return out or ([] if cls and cls.supports_search and not getattr(cls, "browses", False) else [base])


def run_search(job_id: int, more: int = 0):
    """Live product search from the UI. Pulls pages (≈10 ads each) until `limit` ads are collected for each
    connector × country, ingesting every page as it arrives so results appear progressively.
    Resume cursors are kept in job.state: call again with `more` to continue from where it stopped."""
    from .services.engine import rescore_products

    with SessionLocal() as db:
        job = db.get(SearchJob, job_id)
        if more:
            job.limit = more
            job.status = "running"
            job.finished_at = None
            db.commit()
        target = job.limit or 100
        conns = db.scalars(select(Connector).where(Connector.enabled.is_(True), Connector.adapter.is_not(None))).all()
        conns = [c for c in conns if ConnectorFactory.connectors.get(c.adapter) and ConnectorFactory.connectors[c.adapter].supports_search
                 and (not job.adapters or c.adapter in job.adapters)]
        if not conns:
            job.status, job.error, job.finished_at = "error", "Không có connector tìm kiếm nào được bật", datetime.utcnow()
            db.commit()
            publish("SEARCH_DONE", {"job_id": job_id, "error": job.error}, persist=False)
            return
        pids: set[int] = set(job.product_ids or [])
        errors: list[str] = []
        state = dict(job.state or {})
        countries = job.countries or ["ALL"]
        # "a | b | c" = several keywords: each one is searched on every source (OR), results merge into one job
        kws = [k.strip() for k in (job.query or "").split("|") if k.strip()]
        plist = [(kw, FetchParams(query=kw, countries=countries, limit=target, media_type=job.media_type or None)) for kw in kws]
        db.commit()  # close the read transaction before network calls

        # Every source fetches in its own thread (they used to run one after another: Meta's ~3 s pages held up the
        # rest); ingest stays on this thread — SQLite has one writer anyway.
        q: queue.Queue = queue.Queue()

        def worker(cid: int, name: str, adapter: str, cfg: dict):
            _slots.acquire()  # ponytail: one semaphore shared with the scheduler — MAX_PARALLEL_JOBS sources at once in total
            try:
                connector = ConnectorFactory.get(adapter, cfg)
                for kw, params in plist:
                    sfx = f":{kw}" if len(plist) > 1 else ""  # single keyword keeps the old cursor keys
                    if hasattr(connector, "fetch_page"):  # resumable paging (Meta Ad Library)
                        for country in countries:
                            key = f"{cid}:{country}{sfx}"
                            st, got, empty = dict(state.get(key) or {}), 0, 0
                            while got < target and not st.get("done"):
                                recs, st = connector.fetch_page(params, country, st)
                                # Meta returns empty pages mid-stream while the cursor goes on (filtered results):
                                # stopping at the first one lost about half the ads — only a run of them means the end
                                empty = 0 if recs else empty + 1
                                if empty >= 3:
                                    st["done"] = True
                                got += len(recs)
                                q.put(("batch", cid, name, recs, key, dict(st), kw))
                    elif not more:  # single-shot sources (Apify, REST…): only on the first run
                        batch: list = []
                        for rec in connector.fetch_ads(params):
                            batch.append(rec)
                            if len(batch) >= 10:
                                q.put(("batch", cid, name, batch, None, None, kw))
                                batch = []
                        q.put(("batch", cid, name, batch, f"{cid}:*{sfx}", {"done": True}, kw))
                q.put(("end", cid, name, None))
            except Exception as e:
                q.put(("end", cid, name, f"{type(e).__name__}: {e}"))
            finally:
                _slots.release()

        def mark(cid: int, n: int, err: str | None):
            """A live search is a real run of the source: keep its health on the Data & Connectors screen honest."""
            cc = db.get(Connector, cid)
            cc.last_sync_at, cc.last_error = datetime.utcnow(), err
            cc.status, cc.health = ("error", "error") if err else ("ok", "healthy")
            if not err:
                cc.last_sync_count = n
            db.commit()

        for c in conns:
            publish("SEARCH_PROGRESS", {"job_id": job_id, "connector": c.name, "stage": "fetching", "found": job.found}, persist=False)
            threading.Thread(target=worker, args=(c.id, c.name, c.adapter, dict(c.config or {})), daemon=True).start()
        got_by: dict[int, int] = {c.id: 0 for c in conns}
        adapter_by = {c.id: c.adapter for c in conns}
        lost_primary = False  # Meta is the primary source: a lost Meta batch makes the job an error, not "done"
        from .platforms import connector_network

        sources = dict(job.sources or {}) if more else {}
        for c in conns:
            sources.setdefault(c.name, {"network": connector_network(c.adapter, c.config), "fetched": 0, "new": 0, "failed": 0})
            sources[c.name]["error"] = None
        pending = len(conns)
        # job.found = distinct ads stored, not rows received: one ad comes back from several pages / sources / countries
        # ponytail: the set lives for this run only — "load more" can re-count an ad seen in the first run
        seen_ads: set[int] = set()
        while pending:
            kind, cid, name, *rest = q.get()
            if kind == "end":
                pending -= 1
                err = _redact(rest[0])
                if err:
                    errors.append(f"{name}: {err}")
                    sources[name]["error"] = err[:300]
                job.sources = {k: dict(v) for k, v in sources.items()}
                db.commit()
                if not (more and not got_by[cid] and not err):  # "load more" skips single-shot sources
                    mark(cid, got_by[cid], err and err[:500])
                continue
            recs, key, st, kw = rest
            if key:
                state[key] = st
            with _ingest_lock:
                try:
                    for r in recs:
                        r.matched_query = kw
                    res = ingest_ads(db, recs)
                    if res["errors"] and not (res["new_ads"] or res["updated_ads"]):
                        errors.append(f"{name}: lưu vào kho lỗi — {_redact(res['errors'][0])[:200]}")
                    s = sources[name]
                    fresh = set(res["ad_ids"]) - seen_ads
                    seen_ads |= fresh
                    listings = fresh & set(res.get("listing_ids") or [])  # marketplace rows: reported, not counted as ads
                    s["fetched"] += len(recs)
                    s["stored"] = s.get("stored", 0) + len(fresh)
                    s["listings"] = s.get("listings", 0) + len(listings)
                    s["new"] += res["new_ads"]
                    s["failed"] += len(recs) - res["new_ads"] - res["updated_ads"]  # invalid or rejected by the DB
                    job.sources = {k: dict(v) for k, v in sources.items()}
                    job.found += len(fresh) - len(listings)
                    job.new_ads += res["new_ads"]
                    got_by[cid] += len(recs)
                    pids.update(res["product_ids"])
                    job.state = dict(state)
                    job.product_ids = sorted(pids)
                    db.commit()
                except Exception as e:
                    if isinstance(e, OperationalError):
                        log.exception("search job %s: %s lost a batch of %d (sqlite busy)", job_id, name, len(recs))
                        lost_primary |= "meta" in (adapter_by.get(cid) or "")
                    db.rollback()
                    job = db.get(SearchJob, job_id)
                    db.commit()  # db.get opened a write transaction (BEGIN IMMEDIATE): never hold it outside the lock
                    msg = _redact(f"{name}: {type(e).__name__}: {e}")[:300]
                    errors.append(msg)
                    sources[name]["error"] = msg
            if recs:
                publish("SEARCH_PROGRESS", {"job_id": job_id, "connector": name, "found": job.found, "new_ads": job.new_ads,
                                            "products": len(pids)}, persist=False)
        with _ingest_lock:
            job = db.get(SearchJob, job_id)
            rescore_products(db, pids)
            job.state = state
            job.has_more = any(not v.get("done") for v in state.values())
            job.product_ids = sorted(pids)
            job.status = "done" if (job.found or not errors) and not lost_primary else "error"
            job.error = "; ".join(dict.fromkeys(errors))[:1000] or None
            job.finished_at = datetime.utcnow()
            db.commit()
        publish("SEARCH_DONE", {"job_id": job_id, "found": job.found, "new_ads": job.new_ads, "products": len(pids),
                                "has_more": job.has_more, "error": job.error}, persist=False)


# ------------------------------------------------------------ scheduler
_last_daily: datetime | None = None
_last_cleanup = datetime.min
_last_liveness = datetime.min
REFRESH_HOURS = 6  # Tier 3: full re-score + market snapshots
_last_alerts = datetime.min
_last_media = datetime.min
import os as _os

_running: set[str] = set()
MAX_PARALLEL_JOBS = int(_os.getenv("MAX_PARALLEL_JOBS", "2"))
_slots = threading.BoundedSemaphore(MAX_PARALLEL_JOBS)  # scheduled jobs + live-search worker threads, in total


async def scheduler_loop():
    global _last_daily, _last_alerts, _last_media, _last_cleanup, _last_liveness
    await asyncio.sleep(5)
    while True:
        try:
            now = datetime.utcnow()
            from .db import read_only

            with SessionLocal() as db, read_only():
                due = []
                for c in db.scalars(select(Connector).where(Connector.enabled.is_(True), Connector.adapter.is_not(None),
                                                            Connector.every_minutes.is_not(None))).all():
                    if c.adapter == "export":
                        # watch folder: only "due" when a new file is actually waiting (otherwise it starved real jobs)
                        if _export_has_files(c):
                            due.append((datetime.min, "c", c.id, None))
                    elif not c.last_sync_at or now - c.last_sync_at >= timedelta(minutes=c.every_minutes):
                        due.append((c.last_sync_at or datetime.min, "c", c.id, None))
                for q in db.scalars(select(TrackedQuery).where(TrackedQuery.enabled.is_(True))).all():
                    if not q.last_run_at or now - q.last_run_at >= timedelta(minutes=q.every_minutes):
                        due.append((q.last_run_at or datetime.min, "q", q.connector_id, q))
                due.sort(key=lambda x: x[0])  # most overdue first
                due = [d[1:] for d in due]
            for kind, cid, q in due:
                if len(_running) >= MAX_PARALLEL_JOBS:
                    break  # the rest run on the next ticks — keeps SQLite writes and Meta rate limits sane
                key = f"{kind}:{cid}:{q.id if q else ''}"
                if key in _running or cid is None:
                    continue
                _running.add(key)
                asyncio.get_running_loop().run_in_executor(None, _job_wrapper, key, cid, q)
            if now - _last_media > timedelta(minutes=2):
                from .media import enqueue_pending

                await asyncio.to_thread(enqueue_pending)
                _last_media = now
            if now - _last_alerts > timedelta(minutes=15):
                await asyncio.to_thread(_alerts_job)
                _last_alerts = now
            if now - _last_liveness >= timedelta(minutes=20) and len(_running) < MAX_PARALLEL_JOBS:
                _last_liveness = now
                _running.add("liveness")
                asyncio.get_running_loop().run_in_executor(None, _liveness_wrapper)
            from .retention import day_start

            if now - _last_cleanup >= timedelta(hours=6) or _last_cleanup < day_start(now):  # + right after midnight
                await asyncio.to_thread(_cleanup_job)
                _last_cleanup = now
            if _last_daily is None or now - _last_daily >= timedelta(hours=REFRESH_HOURS):
                await asyncio.to_thread(_daily_job)
                _last_daily = now
        except Exception:
            log.exception("scheduler tick failed")
        await asyncio.sleep(30)


def _export_has_files(c: Connector) -> bool:
    from .storage import DATA_DIR

    folder = DATA_DIR / "import" / str((c.config or {}).get("source") or "export")
    return folder.is_dir() and any(f.is_file() and f.suffix.lower() in (".csv", ".json", ".xlsx") for f in folder.iterdir())


def _job_wrapper(key, cid, q):
    try:
        with _slots:
            run_connector(cid, query=q)
    finally:
        _running.discard(key)


def _cleanup_job():
    from .retention import run_cleanup

    with SessionLocal() as db:
        r = run_cleanup(db)
        if r["archived"] or r["raw_deleted"] or r["events_deleted"]:
            log.info("retention: %s", r)
    if r["archived"]:
        publish("STORAGE_ROLLOVER", {"archived": r["archived"], "freed_bytes": r["freed_bytes"]}, persist=False)
    from .media import enqueue_pending

    enqueue_pending()  # start filling the new day's batch right away


def _liveness_wrapper():
    try:
        with _slots:
            r = verify_liveness()
        if r["advertisers"]:
            log.info("liveness: %s", r)
            publish("LIVENESS_CHECKED", r, persist=False)
    except Exception:
        log.exception("liveness failed")
    finally:
        _running.discard("liveness")


def _alerts_job():
    from .services.alerts import run_alerts

    with SessionLocal() as db:
        run_alerts(db)
        db.commit()


def _daily_job():
    """Tier 3: full re-score + daily snapshot (keeps history for growth / acceleration)."""
    from .services.engine import build_snapshots, score_all

    with SessionLocal() as db:
        score_all(db, commit_every=50)
        db.commit()
        build_snapshots(db, commit_every=50)
        db.commit()


# ------------------------------------------------------------ liveness (blueprint §7, §18; facebook_ad_library _last_updated)
LIVENESS_STALE_HOURS = 6     # re-verify active ads not confirmed for this long
LIVENESS_PAGES_PER_RUN = 12  # advertisers checked per run (each ≈ 1-5 Ad Library requests)
LIVENESS_MAX_PAGES = 5       # result pages per advertiser (≈10 ads each); beyond that the list is "incomplete"


def verify_liveness(max_advertisers: int = LIVENESS_PAGES_PER_RUN) -> dict:
    """Ask Meta for each stale advertiser's *currently active* ads and reconcile.

    Never infers "stopped" from silence alone: an ad is marked inactive only when the advertiser's full active
    list was fetched successfully twice in a row without it. Errors / truncated lists change nothing.
    """
    from .collector.adapters.meta import MetaLibraryConnector
    from .models import Ad, AdSource
    from .services.engine import rescore_products

    out = {"advertisers": 0, "verified": 0, "stopped": 0, "suspect": 0, "incomplete": 0, "errors": 0}
    now = datetime.utcnow()
    with SessionLocal() as db:
        c = db.scalar(select(Connector).where(Connector.adapter == "meta_library"))
        if not c or not c.enabled:
            return out
        stale = now - timedelta(hours=LIVENESS_STALE_HOURS)
        rows = db.execute(
            select(Ad.page_id).join(AdSource, AdSource.ad_id == Ad.id)
            .where(Ad.is_active.is_(True), Ad.page_id.is_not(None), AdSource.source == "meta_library",
                   (Ad.last_verified_at.is_(None)) | (Ad.last_verified_at < stale))
            .order_by(Ad.in_target.desc(), Ad.last_verified_at.asc().nullsfirst())
        ).all()
        pages: list[str] = []
        for (pid,) in rows:
            if pid not in pages:
                pages.append(pid)
            if len(pages) >= max_advertisers:
                break
        connector = MetaLibraryConnector(c.config or {})
        db.commit()  # end the read transaction before the network calls

        touched: set[int] = set()
        for pid in pages:
            out["advertisers"] += 1
            seen: set[str] = set()
            st: dict = {}
            recs_all = []
            try:
                for _ in range(LIVENESS_MAX_PAGES):
                    recs, st = connector.fetch_page(FetchParams(page_ids=[pid], active_only=True), "ALL", st)
                    recs_all += recs
                    seen |= {r.source_ad_id for r in recs} | set(st.get("siblings") or [])
                    if st.get("done"):
                        break
            except Exception:
                out["errors"] += 1
                continue
            try:
                _reconcile_page(db, pid, recs_all, seen, bool(st.get("done")), now, out, touched)
            except OperationalError:
                log.exception("liveness: page %s lost (sqlite busy)", pid)
                db.rollback()
                out["errors"] += 1
        if touched:
            from .services import adsignals

            with _ingest_lock:
                adsignals.refresh(db, [a.id for a in db.scalars(select(Ad).where(Ad.product_id.in_(touched)))])
                db.commit()
                rescore_products(db, touched)
                db.commit()
    return out


def _reconcile_page(db, pid, recs_all, seen, complete, now, out, touched):
    from .models import Ad, AdSource

    with _ingest_lock:
        if recs_all:
            # refresh only ads we already track: the scan is worldwide ("ALL"), so a new ad from it would have no country
            # and still count as in-target (audit H4) — new ads come from country searches
            known = set(db.scalars(select(AdSource.source_ad_id).where(
                AdSource.source == "meta_library", AdSource.source_ad_id.in_([r.source_ad_id for r in recs_all]))))
            recs_all = [r for r in recs_all if r.source_ad_id in known]
        if recs_all:
            stats = ingest_ads(db, recs_all)  # refreshes last_verified_at / is_active
            touched |= set(stats["product_ids"])
            out["verified"] += len(seen)
        if not complete:
            out["incomplete"] += 1
            db.commit()
            return
        for ad in db.scalars(select(Ad).join(AdSource, AdSource.ad_id == Ad.id).where(
                Ad.page_id == pid, Ad.is_active.is_(True), AdSource.source == "meta_library")).all():
            src_ids = {s.source_ad_id for s in db.scalars(select(AdSource).where(AdSource.ad_id == ad.id))}
            if src_ids & seen:
                continue
            ad.verify_misses = (ad.verify_misses or 0) + 1
            if ad.verify_misses >= 2:
                ad.is_active, ad.inactive_at = False, now
                out["stopped"] += 1
                touched.add(ad.product_id)
                publish("AD_STOPPED", {"ad_id": ad.id, "page_id": pid}, product_id=ad.product_id, db=db)
            else:
                out["suspect"] += 1
        db.commit()


def freshness(ts: datetime | None) -> str:
    """Blueprint §26 buckets."""
    if not ts:
        return "unreliable"
    age = (datetime.utcnow() - ts).total_seconds() / 3600
    return "fresh" if age < 1 else "recent" if age < 6 else "stale" if age < 24 else "unreliable"
