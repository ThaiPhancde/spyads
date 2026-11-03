"""Meta collector upgrade (Phases 1-5) acceptance on isolated SQLite / API / Next instances (ports 8014 / 3004).
Never touches the real database. Real HTTP + a real browser: extension ingest idempotency, crawl lease vs manual sync,
recovery of a dead worker's lease, KPI endpoint numbers and the "Giám sát" tab of Data & Connectors.

    cd apps/api && .venv/Scripts/python scripts/verify_collector_browser.py
"""
import os
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
API = ROOT / "apps/api"
WEB = ROOT / "apps/web"
TMP = Path(tempfile.mkdtemp(prefix="toolspy-collector-browser-"))
sys.path.insert(0, str(API))
ADMIN, INGEST = "collector-acceptance-admin-token", "collector-acceptance-ingest-token"
os.environ.update(DATABASE_URL="sqlite:///" + (TMP / "test.db").as_posix(), DATA_DIR=str(TMP / "data"), DISABLE_SCHEDULER="1",
                  AUTH_COOKIE_SECURE="0", APP_URL="http://localhost:3004", WEB_ORIGIN="http://localhost:3004",
                  API_URL="http://127.0.0.1:8014", NEXT_DIST_DIR=".next-collector-test", ADMIN_TOKEN=ADMIN, INGEST_TOKEN=INGEST,
                  SMTP_HOST="", BASIC_AUTH_USER="", BASIC_AUTH_PASS="", SERPAPI_API_KEY="", BRIGHTDATA_API_TOKEN="",
                  GROK_ROUTINE_WEBHOOK_URL="", ANTHROPIC_API_KEY="", GEMINI_API_KEY="", GROQ_API_KEY="", CEREBRAS_API_KEY="",
                  CLOUDFLARE_API_TOKEN="", BRIGHTDATA_ENABLED="0", VIDEO_DOWNLOAD="0")
from app import models  # noqa: E402,F401
from app.db import Base, SessionLocal, engine, migrate_all  # noqa: E402
from app.workspace import models as workspace_models  # noqa: E402,F401
from app.workspace.auth import hash_password  # noqa: E402
from app.workspace.models import Profile, User  # noqa: E402
from app.models import Connector, Creative, SearchJob, TrackedQuery  # noqa: E402
from app import crawl_queue as cq  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
import httpx  # noqa: E402

Base.metadata.create_all(engine)
migrate_all()
password = "Collector acceptance password 123!"
now = datetime.utcnow()
with SessionLocal.begin() as db:
    u = User(name="mkt-a", email="mkt-a@example.test", password_hash=hash_password(password), role="MKT", status="active")
    db.add(u); db.flush(); db.add(Profile(user_id=u.id))
    meta = Connector(name="Meta Ad Library", provider="meta", kind="browser", group="transparency", adapter="meta_library",
                     enabled=True, config={}, health="healthy", status="ok", last_sync_at=now)
    tt = Connector(name="TikTok Top Ads (headless)", provider="tiktok", kind="browser", group="ad_intel",
                   adapter="tiktok_top_ads_headless", enabled=True, config={}, health="blocked", status="error",
                   last_error="Blocked: BLOCKED: trang yêu cầu xác minh (CAPTCHA / challenge)")
    db.add_all([meta, tt]); db.flush()
    q = TrackedQuery(connector_id=meta.id, query="blender", countries=["US", "PH"],
                     cursor={"at": now.isoformat(), "pages": {"US": {"cursor": "c2", "done": False}, "PH": {"done": True}}})
    db.add(q); db.flush()
    db.add(SearchJob(query="blender", sources={"Meta Ad Library": {"fetched": 120, "new": 30, "failed": 0}}, created_at=now))
    db.add_all([Creative(type="video", source_url=f"https://cdn.example/{i}", network="meta", status=st, thumb_key=tk)
                for i, (st, tk) in enumerate([("stored", "t"), ("stored", "t"), ("pending", "t"), ("expired", None)])])
    META, TT, QID = meta.id, tt.id, q.id
# crawl history through the real queue: one finished tracked-query run, one blocked TikTok run, one dead worker's lease
t = cq.claim(f"q:{META}:{QID}", "q", META, QID, checkpoint={"pages": {"US": {"cursor": "c1"}}}, worker="w-ok")
cq.finish(f"q:{META}:{QID}", t, {"fetched": 30, "new_ads": 10, "updated_ads": 18, "invalid": 2, "excluded_entertainment": 1,
                                 "pages": 3, "empty_pages": 1},
          checkpoint={"at": now.isoformat(), "pages": {"US": {"cursor": "c2", "done": False}, "PH": {"done": True}}})
cq.fail(f"c:{TT}:", cq.claim(f"c:{TT}:", "c", TT, worker="w-tt"), "Blocked: BLOCKED: CAPTCHA", blocked=True)
cq.claim(f"c:{META}:", "c", META, worker="w-dead", now=now - timedelta(minutes=10))  # died: lease long expired

for port in (8014, 3004):
    with socket.socket() as s:
        assert s.connect_ex(("127.0.0.1", port)) != 0, f"Test port {port} already occupied"
processes, logfiles, results = [], [], []
original_tsconfig = (WEB / "tsconfig.json").read_bytes()
BASE, APIB = "http://localhost:3004", "http://127.0.0.1:8014"
SVC = {"x-admin-token": ADMIN}


def check(cond, what):
    assert cond, what
    results.append(what)


try:
    for name, cmd, cwd in (
        ("api", [sys.executable, "-m", "uvicorn", "app.main:app", "--port", "8014", "--host", "127.0.0.1"], API),
        ("web", ["node", str(WEB / "node_modules/next/dist/bin/next"), "dev", "-p", "3004"], WEB),
    ):
        out = (TMP / f"{name}.log").open("w", encoding="utf-8"); logfiles.append(out)
        processes.append(subprocess.Popen(cmd, cwd=cwd, stdout=out, stderr=subprocess.STDOUT,
                                          creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0))
    for _ in range(180):
        try:
            if httpx.get(f"{APIB}/api/health", timeout=2).status_code == 200 and httpx.get(f"{BASE}/login/", timeout=5).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(1)
    else:
        raise RuntimeError(f"Test servers did not start; logs at {TMP}")

    # Phase 1 — extension batch ingest is idempotent over real HTTP
    item = {"ad_archive_id": "9001", "page_id": "p1", "collation_id": "g1",
            "snapshot": {"title": "Portable electric blender", "body": {"text": "Buy blender now"}}}
    body = {"items": [item], "country": "US", "idempotency_key": "ext-batch-1"}
    hdr = {"X-Ingest-Token": INGEST}
    r1 = httpx.post(f"{APIB}/api/ingest/meta-library", json=body, headers=hdr, timeout=60)
    r2 = httpx.post(f"{APIB}/api/ingest/meta-library", json=body, headers=hdr, timeout=60)
    check(r1.status_code == 200 and r1.json()["new_ads"] == 1 and r2.json() == r1.json(), "extension retry with same key → same receipt, 1 ad")
    r3 = httpx.post(f"{APIB}/api/ingest/meta-library", json={**body, "items": [{**item, "ad_archive_id": "9002"}]}, headers=hdr, timeout=60)
    check(r3.status_code == 409, "same key, different payload → 409")
    check(httpx.post(f"{APIB}/api/ingest/meta-library", json=body, timeout=30).status_code == 401, "ingest without token → 401")

    # Phase 3 — the dead worker's lease is reclaimable; a held lease blocks a manual sync
    check(httpx.get(f"{APIB}/api/collector/kpis", timeout=30).status_code == 401, "KPIs without session / token → 401")
    check(cq.recover() == 1 and cq.claim(f"c:{META}:", "c", META, worker="w-live") is None,
          "dead worker's expired lease → run lost, job backs off (no hot retry)")
    with SessionLocal.begin() as db:  # skip the backoff
        db.get(cq.CrawlJob, f"c:{META}:").next_retry_at = datetime.utcnow()
    held = cq.claim(f"c:{META}:", "c", META, worker="w-live")
    check(held is not None, "after the backoff a new worker takes the crawl")
    r = httpx.post(f"{APIB}/api/collector/connectors/{META}/sync", headers=SVC, timeout=30)
    check(r.json() == {"queued": False, "already_running": True}, "manual sync while the worker holds the lease → already_running")
    cq.finish(f"c:{META}:", held, {"fetched": 0, "new_ads": 0, "updated_ads": 0, "pages": 1, "empty_pages": 1})

    # Phase 5 — KPI numbers over HTTP
    k = httpx.get(f"{APIB}/api/collector/kpis?hours=24", headers=SVC, timeout=30).json()
    row = {c["id"]: c for c in k["connectors"]}
    s = row[META]["scheduled"]
    check((s["pages"], s["empty_pages"], s["fetched"], s["accepted"], s["duplicates"], s["invalid"], s["excluded"]) == (4, 2, 30, 28, 18, 2, 1),
          "KPI: pages 4 (2 empty) · fetched 30 · accepted 28 · duplicates 18 · invalid 2 · excluded 1")
    check((s["ok"], s["lost"]) == (2, 1), "KPI: 2 ok runs + 1 lost run (dead worker)")
    check(row[META]["live_search"]["fetched"] == 120, "KPI: live search volume 120")
    check(row[TT]["health"] == "blocked" and row[TT]["jobs"][0]["status"] == "blocked", "KPI: TikTok blocked, job blocked")
    check(k["media"]["meta"]["available_pct"] == 75.0, "KPI: media 3/4 viewable")

    # Phase 5 — the "Giám sát" tab in a real browser
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="msedge", headless=True)
        ctx = browser.new_context(viewport={"width": 1500, "height": 1300}, extra_http_headers=SVC)
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        # The layout needs an MKT session, but the system area (/api/collector*) answers the internal service token only and
        # web/middleware.js strips x-admin-token from browser requests on purpose (a browser is never elevated). So the
        # browser's collector calls are answered from the API directly with the service token — exactly what it returns.
        def from_api(route):
            u = route.request.url.split("/api/", 1)[1]
            rr = httpx.request(route.request.method, f"{APIB}/api/{u}", headers=SVC, content=route.request.post_data_buffer, timeout=60)
            route.fulfill(status=rr.status_code, body=rr.content, headers={"content-type": rr.headers.get("content-type", "application/json")})
        page.route("**/api/collector**", from_api)
        page.goto(f"{BASE}/data/")
        page.wait_for_url("**/login/**")
        page.get_by_label("Email", exact=True).fill("mkt-a@example.test")
        page.locator("input[name='password']").fill(password)
        page.get_by_role("button", name="Đăng nhập ToolSpy", exact=True).click()
        page.wait_for_url(lambda u: "/login" not in u)
        page.goto(f"{BASE}/data/")
        try:
            page.get_by_role("tab", name="Giám sát").click(timeout=60000)
        except Exception:
            page.screenshot(path=str(TMP / "data_page_failure.png"), full_page=True)
            print("URL", page.url, "screenshot", TMP / "data_page_failure.png", "errors", errors[:3])
            for label, url in (("direct", f"{APIB}/api/collector"), ("via next", f"{BASE}/api/collector")):
                rr = httpx.get(url, headers=SVC, timeout=30)
                print("DIAG", label, rr.status_code, sorted(rr.json())[:8] if rr.headers.get("content-type", "").startswith("application/json") else rr.text[:200])
            raise
        page.get_by_text("Trang đã lấy").wait_for(timeout=60000)
        page.wait_for_load_state("networkidle")
        mon = page.get_by_role("tabpanel")
        for text in ("Thu thập — 24 giờ qua", "Ads chấp nhận", "Không hợp lệ", "Bị chặn (cần xác minh)", "Bị chặn",
                     "cursor 1/2 nước còn trang · 1 xong", "Media có thể xem", "75%", "2 ok · 1 lỗi · 1 mất lease"):
            check(mon.get_by_text(text, exact=False).count() > 0, f"tab Giám sát shows “{text}”")
        page.screenshot(path=str(TMP / "monitoring.png"), full_page=True)
        page.get_by_role("tab", name="Nguồn & connector").click()
        page.get_by_text("Meta Ad Library").first.wait_for()
        check(not errors, f"no page errors ({errors[:2]})")
        browser.close()
    print("\n".join(f"PASS  {r}" for r in results))
    print(f"\n{len(results)} checks passed · screenshot {TMP / 'monitoring.png'}")
finally:
    for process in processes:
        if process.poll() is None:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                process.terminate()
            process.wait(timeout=15)
    for f in logfiles:
        f.close()
    (WEB / "tsconfig.json").write_bytes(original_tsconfig)
