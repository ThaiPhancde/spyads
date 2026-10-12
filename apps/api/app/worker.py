"""Scheduler process — `python -m app.worker` (run the API with DISABLE_SCHEDULER=1).

Scheduled syncs, tracked queries, liveness, spy scans, media queue, alerts, retention and the 6-hourly re-score run
here, so a heavy tick never competes with user requests (live searches stay in the API process).
Run exactly one worker: the scheduler keeps its "what is due / running" state in memory.
"""
import asyncio
import logging

from .db import auto_migrate
from .media import requeue_lost
from .realtime import scheduler_loop

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
for noisy in ("httpx", "httpcore"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

if __name__ == "__main__":
    auto_migrate()
    logging.getLogger("app.worker").info("media: %s lost creatives re-queued", requeue_lost())
    asyncio.run(scheduler_loop())  # enqueue_pending runs on its first tick
