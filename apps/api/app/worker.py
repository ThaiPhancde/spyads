"""Scheduler process — `python -m app.worker` (run the API with DISABLE_SCHEDULER=1).

Scheduled syncs, tracked queries, liveness, spy scans, media queue, retention and the 6-hourly re-score run
here, so a heavy tick never competes with user requests (live searches stay in the API process).
Exactly one scheduler runs per database (Postgres advisory lock, db.scheduler_lock); a second worker exits.
Its state is published every tick as a heartbeat in app_settings (realtime.write_heartbeat).
"""
import asyncio
import logging

from .db import migrate_all, scheduler_lock
from .media import requeue_lost
from .realtime import scheduler_loop

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
for noisy in ("httpx", "httpcore"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

if __name__ == "__main__":
    log = logging.getLogger("app.worker")
    _lock = scheduler_lock()  # held (connection open) for the whole process lifetime
    if _lock is None:
        log.error("another scheduler already holds the lock (worker, or API without DISABLE_SCHEDULER=1) — exiting")
        raise SystemExit(1)
    migrate_all()
    from .workspace.worker import tracker_loop
    log.info("media: %s lost creatives re-queued", requeue_lost())
    from .preferences import loop as preference_loop
    from .recommend import loop as recommendation_loop
    async def run():
        await asyncio.gather(scheduler_loop(), tracker_loop(), preference_loop(), recommendation_loop())
    asyncio.run(run())
