"""Phase 4: browser worker — child process per task, CPU/RAM cap, hard timeout kills the tree, blocked / rate_limited."""
import json
import os
import sys
import threading
import time

import pytest

from app import browser_worker as bw
from app.collector.base import Blocked, ConnectorError, RateLimited


def alive(pid: int) -> bool:
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    import ctypes
    h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    code = ctypes.c_ulong()
    ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
    ctypes.windll.kernel32.CloseHandle(h)
    return code.value == 259  # STILL_ACTIVE


def fake_child(monkeypatch, body: str):
    """Replace the child command with a tiny script; `out` is the result path the parent reads."""
    monkeypatch.setattr(bw, "_cmd", lambda task, src, dst: [sys.executable, "-c", f"out = r'{dst}'\n" + body])


def test_capacity_follows_cpu_ram_and_cap(monkeypatch):
    monkeypatch.setenv("BROWSER_MAX_CONCURRENCY", "4")
    assert bw.capacity(cpus=16, free_mb=100_000) == 4
    assert bw.capacity(cpus=4, free_mb=100_000) == 2  # half the CPUs
    assert bw.capacity(cpus=16, free_mb=1500) == 2  # 700 MB per browser
    assert bw.capacity(cpus=1, free_mb=100) == 1  # never zero: the task waits for its slot instead
    assert bw._free_ram_mb() is None or bw._free_ram_mb() > 0


def test_health_task_runs_a_real_browser_in_a_child(monkeypatch):
    monkeypatch.setenv("BROWSER_CHANNEL", "chromium")  # the Playwright build in CI; servers use installed Edge / Chrome
    data = bw.run("health", timeout=120)
    assert data["text"] == "ok" and data["browser"]


def test_timeout_kills_the_whole_process_tree(monkeypatch, tmp_path):
    pidfile = tmp_path / "grandchild.pid"
    fake_child(monkeypatch, f"""
import subprocess, sys, time
g = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
open(r'{pidfile}', 'w').write(str(g.pid))
time.sleep(120)
""")
    t0 = time.time()
    with pytest.raises(ConnectorError, match="BROWSER_TIMEOUT"):
        bw.run("health", timeout=3)
    assert time.time() - t0 < 30
    gpid = int(pidfile.read_text())
    for _ in range(20):
        if not alive(gpid):
            break
        time.sleep(0.25)
    assert not alive(gpid), "the browser-like grandchild outlived the timeout"


@pytest.mark.parametrize("status, exc", [("blocked", Blocked), ("rate_limited", RateLimited), ("error", ConnectorError)])
def test_child_status_becomes_typed_error(monkeypatch, status, exc):
    fake_child(monkeypatch, f"import json; open(out, 'w').write(json.dumps({{'status': '{status}', 'error': 'x'}}))")
    with pytest.raises(exc, match=status.upper()):
        bw.run("health")


def test_child_crash_without_result_is_an_error(monkeypatch):
    fake_child(monkeypatch, "import sys; sys.stderr.write('boom\\n'); sys.exit(3)")
    with pytest.raises(ConnectorError, match="BROWSER_ERROR.*exit 3.*boom"):
        bw.run("health")


def test_busy_when_every_slot_is_taken(monkeypatch):
    gate = threading.BoundedSemaphore(1)
    monkeypatch.setattr(bw, "_slots", gate)
    gate.acquire()
    try:
        with pytest.raises(ConnectorError, match="BROWSER_BUSY"):
            bw.run("health", wait=0.1)
    finally:
        gate.release()


def test_only_registered_tasks_run():
    with pytest.raises(ValueError):
        bw.run("https://example.com")


def test_challenge_and_429_are_reported_never_bypassed():
    pw = pytest.importorskip("playwright.sync_api")
    with pw.sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        try:
            pg = b.new_page()
            pg.route("https://source.test/**", lambda r: r.fulfill(status=429, body="{}") if "api" in r.request.url
                     else r.fulfill(content_type="text/html", body="<p>ads</p><script>fetch('/api/list')</script>"))
            w = bw.Watch(pg, "/api/list", lambda j: None)
            pg.goto("https://source.test/page")
            pg.wait_for_timeout(500)
            with pytest.raises(RateLimited):
                w.check(pg, None, [])
            w.check(pg, None, [{"some": "data"}])  # 429 on a retry after data arrived is not a failed task
            pg.set_content("<div id='captcha-verify-image'>slide to verify</div>")
            with pytest.raises(Blocked):
                w.check(pg, None, [{"some": "data"}])
        finally:
            b.close()


def test_blocked_source_marks_health_and_stops_fast_retries(clean_catalog, monkeypatch):
    from app import crawl_queue as cq, realtime
    from app.db import SessionLocal
    from app.models import Connector

    class Fake:
        def fetch_ads(self, params):
            raise Blocked("trang yêu cầu xác minh (CAPTCHA / challenge)")
            yield
    monkeypatch.setattr(realtime.ConnectorFactory, "get", lambda *a: Fake())
    with SessionLocal() as db:
        c = Connector(name="TT", provider="tiktok", kind="browser", group="ad_intel", adapter="tiktok_top_ads_headless",
                      enabled=True, config={})
        db.add(c); db.commit()
        cid = c.id
    key = realtime.job_key(cid)
    realtime._job_wrapper(key, cid, None, realtime.claim_crawl(cid))
    with SessionLocal() as db:
        assert db.get(Connector, cid).health == "blocked"
        job = db.get(cq.CrawlJob, key)
        assert job.status == "blocked" and job.next_retry_at is None
    assert key not in cq.retry_due()


def test_meta_rate_limit_is_typed(monkeypatch):
    from types import SimpleNamespace
    from app.collector.adapters.meta import MetaLibraryConnector
    from app.collector.base import FetchParams
    c = MetaLibraryConnector({})
    monkeypatch.setattr(c, "authenticate", lambda: None)
    monkeypatch.setattr(c.bucket, "take", lambda: None)
    c._col = SimpleNamespace(client=SimpleNamespace(search_ads=lambda **kw: ({"ads": [], "page_info": {}, "rate_limited": True}, None)))
    with pytest.raises(RateLimited):
        c.fetch_page(FetchParams(), "US")
