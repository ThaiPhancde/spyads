"""In-process event bus (realtime_product_fit_summary §2, §5).

Every important change becomes an event: persisted in `events` (history/feed) and fanned
out to Server-Sent-Event subscribers (the web app) in < 1 s. Safe to publish from worker
threads. For multi-instance deployments swap the fan-out for Redis pub/sub.
"""
from __future__ import annotations

import asyncio
import json
import threading
from datetime import datetime

EVENT_TYPES = [
    "ORDER_CREATED", "ORDER_CONFIRMED", "ORDER_CANCELLED",
    "SHIPMENT_CREATED", "SHIPMENT_DELIVERED", "SHIPMENT_FAILED", "SHIPMENT_REFUSED", "SHIPMENT_RETURNED",
    "COMMENT_CREATED", "NEGATIVE_SENTIMENT_SPIKE",
    "PRODUCT_DISCOVERED", "PRODUCT_GROWTH_SPIKE", "RARE_WINNER_DETECTED", "DECISION_CHANGED",
    "AD_CREATED", "AD_REJECTED", "CREATIVE_STORED",
    "ROAS_DROP", "REFUSAL_SPIKE", "DELIVERY_RATE_DROP",
    "CONNECTOR_SYNCED", "CONNECTOR_FAILED", "SEARCH_PROGRESS", "SEARCH_DONE", "ALERT",
]

_subs: list[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = []
listeners: list = []  # in-process callbacks(type_) — e.g. router cache invalidation
_lock = threading.Lock()


def subscribe() -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=500)
    with _lock:
        _subs.append((asyncio.get_running_loop(), q))
    return q


def unsubscribe(q: asyncio.Queue):
    with _lock:
        _subs[:] = [(l, s) for l, s in _subs if s is not q]


def _fanout(payload: dict):
    with _lock:
        subs = list(_subs)
    for loop, q in subs:
        def put(q=q):
            if q.full():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            q.put_nowait(payload)
        try:
            loop.call_soon_threadsafe(put)
        except RuntimeError:
            pass


def publish(type_: str, data: dict | None = None, product_id: int | None = None, persist: bool = True, db=None):
    """Publish an event. Pass `db` to persist inside the caller's transaction."""
    payload = {"type": type_, "product_id": product_id, "data": data or {}, "at": datetime.utcnow().isoformat() + "Z"}
    if persist:
        from .models import Event

        if db is not None:
            db.add(Event(type=type_, product_id=product_id, data=data or {}))
        else:
            from .db import SessionLocal

            with SessionLocal() as s:
                s.add(Event(type=type_, product_id=product_id, data=data or {}))
                s.commit()
    _fanout(payload)
    for cb in list(listeners):
        try:
            cb(type_)
        except Exception:
            pass


def sse_format(payload: dict) -> str:
    return f"event: {payload['type']}\ndata: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"
