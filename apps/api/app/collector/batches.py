"""Transactional ingest. Caller holds ingest_lock and commits checkpoint + receipt together."""
import hashlib
import json

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..ingest import ingest_ads
from ..models import IngestBatch


class BatchConflict(ValueError):
    pass


def digest(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def ingest_batch(db, records, key=None, saved_by=None, payload=None, envelope=None):
    """key=None: atomic ingest without a receipt (server-side crawls: the checkpoint commit is their idempotency).
    envelope (P1): source / job_id / batch_id / fetched_at / schema_version / cursor_before / cursor_after, kept on the receipt;
    one (job_id, batch_id) is one delivery — the same pair under another key is a conflict, never a second copy."""
    if key is None:
        db.info["collector_atomic"] = True
        return ingest_ads(db, records, saved_by=saved_by, allow_llm=False, atomic=True)
    fingerprint = digest(payload if payload is not None else [r.to_dict() for r in records])
    prior = db.get(IngestBatch, key)
    if prior:
        if prior.payload_hash != fingerprint:
            raise BatchConflict("Idempotency key already used with a different payload")
        return dict(prior.result)
    env = {k: v for k, v in (envelope or {}).items() if v is not None}
    if env.get("job_id") and env.get("batch_id") and db.scalar(select(IngestBatch.key).where(
            IngestBatch.job_id == env["job_id"], IngestBatch.batch_id == env["batch_id"])):
        raise BatchConflict("This job / batch was already delivered under another idempotency key")
    db.info["collector_atomic"] = True
    result = ingest_ads(db, records, saved_by=saved_by, allow_llm=False, atomic=True)
    db.add(IngestBatch(key=key, payload_hash=fingerprint, result=result, **env))
    try:
        db.flush()
    except IntegrityError as e:  # a concurrent writer delivered the same job / batch first (UNIQUE(job_id, batch_id))
        raise BatchConflict("This job / batch was already delivered") from e
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
