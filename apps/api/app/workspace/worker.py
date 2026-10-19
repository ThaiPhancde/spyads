"""Small durable scheduler. Source calls run in killable child processes, never in request threads."""
import asyncio
import logging
import os
import secrets
import subprocess
import sys
from datetime import datetime, timedelta

from sqlalchemy import delete, exists, func, select, text

from ..db import SessionLocal, ingest_lock
from ..models import Advertiser
from .models import (AuthRateLimit, CsrfToken, Follow, PasswordReset, ScanJob, SourceQuota, UserSession)
from .sources import Unsupported, fetch
from .tracking import apply_scan, event

log = logging.getLogger(__name__)


def timeout_seconds():
    return max(30, min(int(os.getenv("COMPETITOR_TIMEOUT_SECONDS", "180")), 900))


def lock_scheduler(db):
    if db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(727019)"))


def interval(db, aid):
    return db.scalar(select(func.min(Follow.interval_hours)).where(Follow.advertiser_id == aid)) or 24


def claim_due(now=None):
    now = now or datetime.utcnow()
    with SessionLocal.begin() as db:
        lock_scheduler(db)
        cap = max(1, min(int(os.getenv("COMPETITOR_CONCURRENCY", "1")), 4))
        active = db.scalar(select(func.count()).select_from(ScanJob).where(ScanJob.lease_until > now))
        if active >= cap:
            return None
        jobs = db.scalars(select(ScanJob).where(ScanJob.next_check_at <= now,
            (ScanJob.lease_until.is_(None)) | (ScanJob.lease_until <= now),
            exists(select(Follow.user_id).where(Follow.advertiser_id == ScanJob.advertiser_id)))
            .order_by(ScanJob.next_check_at).limit(100).with_for_update(skip_locked=True)).all()
        for job in jobs:
            # One advertiser per source at a time, even across scheduler processes.
            if db.scalar(select(ScanJob.advertiser_id).where(ScanJob.source == job.source, ScanJob.lease_until > now).limit(1)):
                continue
            quota = db.get(SourceQuota, job.source)
            if not quota:
                quota = SourceQuota(source=job.source, window_at=now, used=0)
                db.add(quota)
            if now >= quota.window_at + timedelta(hours=1):
                quota.window_at, quota.used = now, 0
            limit = max(1, int(os.getenv(f"COMPETITOR_QUOTA_{job.source.upper()}", os.getenv("COMPETITOR_SCANS_PER_SOURCE_HOUR", "10"))))
            if quota.used >= limit:
                job.next_check_at = quota.window_at + timedelta(hours=1)
                job.source_status = "quota_wait"
                continue
            quota.used += 1
            job.run_token = secrets.token_hex(24)
            job.lease_until = now + timedelta(seconds=timeout_seconds() + 60)
            job.last_checked_at, job.source_status = now, "running"
            job.attempts += 1
            return job.advertiser_id, job.run_token
    return None


def fail(aid, token, message, status="error", now=None):
    now = now or datetime.utcnow()
    with SessionLocal.begin() as db:
        job = db.scalar(select(ScanJob).where(ScanJob.advertiser_id == aid).with_for_update())
        if not job or job.run_token != token:
            return
        job.failures += 1
        # Provider exception text can contain signed URLs/tokens; only controlled messages are persisted.
        job.error, job.source_status, job.lease_until, job.run_token = message, status, None, None
        if job.attempts < 3 and status not in ("unsupported", "not_configured"):
            job.next_check_at = now + timedelta(minutes=2 ** job.attempts)
        else:
            job.next_check_at, job.attempts = now + timedelta(hours=interval(db, aid)), 0
        if job.failures == 3:
            event(db, aid, "source_error", f"error:{aid}:{token}", {"message": message})


def run_scan(aid, token):
    from ..collector.base import NotConfigured
    from ..ingest import ingest_ads
    try:
        with SessionLocal() as db:
            job = db.get(ScanJob, aid)
            if not job or job.run_token != token or not job.lease_until or job.lease_until <= datetime.utcnow():
                return
            advertiser = db.get(Advertiser, aid)
            result = fetch(db, advertiser)
            # Recheck the lease before touching shared Ads; stale processes cannot publish observations.
            db.expire_all()
            job = db.get(ScanJob, aid)
            if job.run_token != token or job.lease_until <= datetime.utcnow():
                return
            db.commit()
            if result.records:
                with ingest_lock():
                    stats = ingest_ads(db, result.records, allow_llm=False)
                if stats.get("invalid") or stats.get("errors"):
                    raise RuntimeError("ingest failed")
            job = db.scalar(select(ScanJob).where(ScanJob.advertiser_id == aid).with_for_update())
            if job.run_token != token or job.lease_until <= datetime.utcnow():
                db.rollback()
                return
            changes = apply_scan(db, advertiser, result, token)
            now = datetime.utcnow()
            job.last_changes, job.last_success_at = changes, now
            job.source_status = "success" if result.complete else "partial"
            job.error = None if result.complete else "Đã cập nhật phần dữ liệu nguồn trả về; chưa xác nhận quét đủ."
            job.next_check_at = now + timedelta(hours=interval(db, aid))
            job.lease_until, job.run_token, job.attempts, job.failures = None, None, 0, 0
            event(db, aid, "scan_complete", f"scan:{token}", {"changes": changes, "complete": result.complete}, notify=False)
            db.commit()
    except Unsupported as exc:
        fail(aid, token, str(exc), "unsupported")
    except NotConfigured:
        fail(aid, token, "Nguồn chưa bật hoặc thiếu cấu hình/quyền truy cập. Kiểm tra Data & Connectors.", "not_configured")
    except Exception as exc:
        log.warning("competitor scan %s failed (%s)", aid, type(exc).__name__)
        fail(aid, token, "Không lấy được dữ liệu tin cậy từ nguồn. Lịch sử được giữ lại; hệ thống sẽ thử lại theo lịch.")


async def execute(aid, token):
    # A hard deadline actually terminates network work (unlike wait_for(to_thread(...))).
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    proc = subprocess.Popen([sys.executable, "-m", "app.workspace.worker", "scan", str(aid), token], **options)
    try:
        for _ in range(timeout_seconds()):
            if proc.poll() is not None:
                if proc.returncode:
                    await asyncio.to_thread(fail, aid, token, "Tiến trình quét bị ngắt; sẽ thử lại theo lịch.")
                return
            await asyncio.sleep(1)
        proc.kill()
        await asyncio.to_thread(proc.wait)
        await asyncio.to_thread(fail, aid, token, "Nguồn vượt thời gian chờ; đã dừng lượt quét và giữ lịch sử.", "timeout")
    finally:
        if proc.poll() is None:
            proc.kill()
            await asyncio.to_thread(proc.wait)


def cleanup_auth():
    with SessionLocal.begin() as db:
        now = datetime.utcnow()
        for model in (UserSession, CsrfToken, AuthRateLimit, PasswordReset):
            db.execute(delete(model).where(model.expires_at < now))


async def tracker_loop():
    running = set()
    ticks = 0
    try:
        while True:
            try:
                if ticks % 120 == 0:
                    await asyncio.to_thread(cleanup_auth)
                ticks += 1
                job = await asyncio.to_thread(claim_due)
                if job:
                    task = asyncio.create_task(execute(*job))
                    running.add(task)
                    task.add_done_callback(running.discard)
            except Exception:
                log.exception("competitor scheduler tick failed")
            await asyncio.sleep(30)
    finally:
        for task in running:
            task.cancel()
        await asyncio.gather(*running, return_exceptions=True)


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "scan":
        run_scan(int(sys.argv[2]), sys.argv[3])
    else:
        from .migrate import migrate
        migrate()
        asyncio.run(tracker_loop())
