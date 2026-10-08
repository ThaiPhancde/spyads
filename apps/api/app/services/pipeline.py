"""Product Pipeline (blueprint §25, §27): ingest → normalize → enrich → features → scores →
decisions → snapshots → alerts. In production each step is a worker behind a queue
(Temporal/Airflow/n8n); here they run sequentially in one call."""
import time
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Connector, PipelineRun
from .alerts import run_alerts
from .connectors import classify_order_refusals, enrich_comments, normalize_pending
from .engine import build_snapshots, score_all


def run_pipeline(db: Session, pull_connectors: bool = True) -> PipelineRun:
    run = PipelineRun(started_at=datetime.utcnow(), steps=[])
    db.add(run)
    db.flush()
    steps = []

    def step(name, fn):
        t = time.perf_counter()
        try:
            result = fn()
            steps.append({"step": name, "ok": True, "result": result, "ms": round((time.perf_counter() - t) * 1000)})
        except Exception as ex:
            steps.append({"step": name, "ok": False, "result": f"{type(ex).__name__}: {ex}", "ms": round((time.perf_counter() - t) * 1000)})
            raise

    try:
        if pull_connectors:
            def pull():
                out = {}
                from ..realtime import run_connector

                for c in db.scalars(select(Connector).where(Connector.enabled.is_(True), Connector.adapter.is_not(None))).all():
                    out[c.name] = run_connector(c.id)
                return out
            step("01 Ingest (connectors → raw lake)", pull)
        step("02-06 Normalize · Dedup · Entity resolution · Extract · Market classify", lambda: normalize_pending(db))
        step("09 Comment analysis (backlog)", lambda: {"comments": enrich_comments(db)[0], "refusals": classify_order_refusals(db)})
        step("10-12 Features · Scores · Opportunity · Decisions · Lifecycle", lambda: {"products": score_all(db)})
        step("Daily snapshot", lambda: {"products": build_snapshots(db)})
        step("13 Triggers / Alerts", lambda: {"alerts": run_alerts(db)})
        run.status = "ok"
    except Exception:
        db.rollback()
        run = PipelineRun(started_at=run.started_at, status="error")
        db.add(run)
    run.steps = steps
    run.finished_at = datetime.utcnow()
    db.commit()
    return run
