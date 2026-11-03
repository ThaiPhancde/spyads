"""Transactional ingest. Caller holds ingest_lock and commits checkpoint + receipt together."""
import hashlib
import json
from ..ingest import ingest_ads
from ..models import IngestBatch


class BatchConflict(ValueError):
    pass


def digest(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def ingest_batch(db, records, key, saved_by=None, payload=None):
    fingerprint = digest(payload if payload is not None else [r.to_dict() for r in records])
    prior = db.get(IngestBatch, key)
    if prior:
        if prior.payload_hash != fingerprint:
            raise BatchConflict("Idempotency key already used with a different payload")
        return dict(prior.result)
    db.info["collector_atomic"] = True
    result = ingest_ads(db, records, saved_by=saved_by, allow_llm=False, atomic=True)
    db.add(IngestBatch(key=key, payload_hash=fingerprint, result=result))
    db.flush()
    return result


# Savepoints must not publish success. Only the outer transaction releases effects.
from sqlalchemy import event
from sqlalchemy.orm import Session


@event.listens_for(Session, "after_commit")
def _committed(db):
    if db.in_nested_transaction():
        return
    db.info.pop("collector_atomic", None)
    events = db.info.pop("collector_events", [])
    media_ids = db.info.pop("collector_media", set())
    from ..events import deliver
    for payload in events:
        deliver(payload)
    if media_ids:
        from .. import media
        try:
            media.enqueue(list(media_ids))
        except Exception:
            # Durable pending Creative rows are retried by the existing media scheduler.
            import logging
            logging.getLogger(__name__).exception("Post-commit media enqueue failed")


@event.listens_for(Session, "after_rollback")
def _rolled_back(db):
    db.info.pop("collector_atomic", None)
    db.info.pop("collector_events", None)
    db.info.pop("collector_media", None)
