"""Scheduler process — `python -m app.worker` (run the API with DISABLE_SCHEDULER=1).

Scheduled syncs, tracked queries, liveness, spy scans, media queue, retention and the 6-hourly re-score run
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
    from .workspace.migrate import migrate
    from .workspace.worker import tracker_loop
    migrate()
    from .cluster_migrate import migrate as migrate_clusters
    migrate_clusters()
    logging.getLogger("app.worker").info("media: %s lost creatives re-queued", requeue_lost())
    from .preferences import loop as preference_loop
    from .recommend import loop as recommendation_loop
    async def run():
        await asyncio.gather(scheduler_loop(), tracker_loop(), preference_loop(), recommendation_loop())
    asyncio.run(run())
