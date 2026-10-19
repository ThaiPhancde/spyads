"""Phase 7 browser check of the personalized Search page on isolated SQLite/API/Next instances (ports 8012 / 3002).
Never touches the real database. Two MKTs, a followed advertiser, one product already claimed by the other MKT."""
import os
from datetime import datetime, timedelta
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[3]
API = ROOT / "apps/api"
WEB = ROOT / "apps/web"
TMP = Path(tempfile.mkdtemp(prefix="toolspy-search-browser-"))
sys.path.insert(0, str(API))
os.environ.update(DATABASE_URL="sqlite:///" + (TMP / "test.db").as_posix(), DISABLE_SCHEDULER="1", AUTH_COOKIE_SECURE="0",
                  APP_URL="http://localhost:3002", WEB_ORIGIN="http://localhost:3002", API_URL="http://127.0.0.1:8012",
                  NEXT_DIST_DIR=".next-search-test", SMTP_HOST="", BASIC_AUTH_USER="", BASIC_AUTH_PASS="",
                  SERPAPI_API_KEY="", BRIGHTDATA_API_TOKEN="", GROK_ROUTINE_WEBHOOK_URL="", ANTHROPIC_API_KEY="",
                  GEMINI_API_KEY="", GROQ_API_KEY="", CEREBRAS_API_KEY="", CLOUDFLARE_API_TOKEN="", BRIGHTDATA_ENABLED="0")
from app.workspace.migrate import migrate  # noqa: E402
from app.workspace.auth import hash_password  # noqa: E402
from app.workspace.models import Follow, Profile, User  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import Ad, Advertiser, Product  # noqa: E402
from app.services import cluster_membership as CM  # noqa: E402
from app import allocation as A  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

migrate()
password = "Browser integration password 123!"
now = datetime.utcnow()
with SessionLocal.begin() as db:
    users = {}
    for email in ("mkt-a@example.test", "mkt-b@example.test"):
        u = User(name=email.split("@")[0], email=email, password_hash=hash_password(password), role="MKT", status="active")
        db.add(u); db.flush(); db.add(Profile(user_id=u.id)); users[email] = u.id
    rival = Advertiser(name="Rival Shop", source="meta_library", source_advertiser_id="777", platform="facebook", country="PH")
    db.add(rival); db.flush()
    db.add(Follow(user_id=users["mkt-a@example.test"], advertiser_id=rival.id))
    pids = {}
    for name, cat, lang, adv in (("Polarized sunglasses", "accessories", "en", None), ("Kính mát phân cực", "accessories", "vi", None),
                                 ("Dog leash nylon", "pets", "en", rival.id), ("Taken sunglasses", "accessories", "en", None),
                                 ("Garden hose", "home", "en", None)):
        p = Product(product_code=f"SB-{len(pids)}", canonical_name=name, category=cat, country="PH", language=lang,
                    name_status="AUTO_CONFIRMED", in_target=True, opportunity_score=50, first_seen_at=now - timedelta(days=3),
                    last_seen_at=now, features={"unique_ads": 4, "advertiser_count": 2, "longevity_days": 20, "markets": ["PH"]})
        db.add(p); db.flush()
        a = Ad(external_id=f"sb-{p.id}", source="meta_library", platform="facebook", network="meta", product_id=p.id,
               advertiser_id=adv, title=name, ad_text=name, search_text=name.lower(), country="PH", countries=["PH"],
               language=lang, is_active=True, in_target=True, first_seen_at=now - timedelta(days=3), last_seen_at=now)
        db.add(a); db.flush(); pids[name] = p.id
    CM.sync(db)
with SessionLocal() as db:
    A.claim(db, users["mkt-b@example.test"], pids["Taken sunglasses"]); db.commit()

for port in (8012, 3002):
    with socket.socket() as s:
        assert s.connect_ex(("127.0.0.1", port)) != 0, f"Test port {port} already occupied"
processes, logfiles = [], []
original_tsconfig = (WEB / "tsconfig.json").read_bytes()


def login(page, email):
    page.goto("http://localhost:3002/search/")
    page.wait_for_url("**/login/**")
    page.get_by_label("Email", exact=True).fill(email)
    page.locator("input[name='password']").fill(password)
    page.get_by_role("button", name="Đăng nhập ToolSpy", exact=True).click()
    page.wait_for_url(lambda u: "/login" not in u)
    page.goto("http://localhost:3002/search/")


def cards(page):
    page.wait_for_timeout(500)  # debounce (350 ms) …
    page.wait_for_load_state("networkidle")  # … then the request itself, however slow the machine is
    page.get_by_text("sản phẩm", exact=False).first.wait_for()
    return page.locator("div.grid > div.flex.flex-col")


try:
    for name, cmd, cwd in (
        ("api", [sys.executable, "-m", "uvicorn", "app.main:app", "--port", "8012", "--host", "127.0.0.1"], API),
        ("web", ["node", str(WEB / "node_modules/next/dist/bin/next"), "dev", "-p", "3002"], WEB),
    ):
        out = (TMP / f"{name}.log").open("w", encoding="utf-8"); logfiles.append(out)
        processes.append(subprocess.Popen(cmd, cwd=cwd, stdout=out, stderr=subprocess.STDOUT,
                                          creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0))
    import httpx
    for _ in range(120):
        try:
            if httpx.get("http://127.0.0.1:8012/api/health", timeout=2).status_code == 200 and httpx.get("http://localhost:3002/login/", timeout=5).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(1)
    else:
        raise RuntimeError(f"Test servers did not start; logs at {TMP}")
    results = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="msedge", headless=True)
        ctx = browser.new_context(viewport={"width": 1440, "height": 1100})
        page = ctx.new_page()
        errors, searches = [], []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("request", lambda r: searches.append(r.url) if "/api/search?" in r.url or r.url.endswith("/api/search") else None)
        login(page, "mkt-a@example.test")
        grid = cards(page)
        assert page.get_by_role("radio", name="Tất cả", exact=True).get_attribute("data-state") == "on"
        page.get_by_text("MKT khác đang giữ chỗ", exact=True).first.wait_for()  # "Tất cả": visible, labelled
        assert page.get_by_text("Taken sunglasses").count() >= 1
        results.append("all-mode shows taken product labelled")
        page.get_by_role("radio", name="Có thể nhận").click()
        grid = cards(page)
        page.wait_for_timeout(600)
        assert page.get_by_text("Taken sunglasses").count() == 0 and page.get_by_text("MKT khác đang giữ chỗ", exact=True).count() == 0
        page.screenshot(path=str(TMP / "claimable.png"), full_page=True)
        results.append("claimable hides other MKT's product")
        before = len(searches)
        page.get_by_role("button", name="Nhận", exact=True).first.click()
        page.get_by_text("Đã giữ chỗ sản phẩm cho bạn", exact=True).wait_for()
        mine = page.request.get("http://localhost:3002/api/allocations").json()
        assert mine["total"] == 1, mine  # one click = one product, never bulk
        results.append("one explicit claim → exactly one allocation")
        page.get_by_role("radio", name="Dành cho tôi").click()
        cards(page)
        page.get_by_text("Phù hợp", exact=False).first.wait_for()
        page.locator("text=/Phù hợp \\d+/").first.hover()
        page.wait_for_timeout(400)
        page.screenshot(path=str(TMP / "for_me.png"), full_page=True)
        assert page.get_by_role("radio", name="Dành cho tôi").get_attribute("data-state") == "on"  # active mode is visibly selected
        results.append("for-me shows fit score + reasons tooltip; active mode highlighted")
        page.get_by_role("radio", name="Đối thủ đang Follow").click()
        cards(page)
        page.wait_for_timeout(600)
        assert page.get_by_text("Dog leash nylon").count() >= 1 and page.get_by_text("Garden hose").count() == 0
        results.append("followed mode lists only the followed advertiser's products")
        page.get_by_role("radio", name="Tất cả", exact=True).click()
        cards(page)
        page.get_by_role("button", name="SP tương tự", exact=True).first.click()
        page.get_by_text("Tương tự sản phẩm #", exact=False).wait_for()
        cards(page)
        page.screenshot(path=str(TMP / "similar.png"), full_page=True)
        results.append("per-card 'Tương tự' switches to similar mode with a seed")
        page.get_by_role("radio", name="Tất cả", exact=True).click()
        cards(page)
        n0 = len(searches)
        box = page.get_by_placeholder("Tương tác thực ≥ N / ad")
        box.press_sequentially("123", delay=60)
        page.wait_for_timeout(1200)
        fired = len(searches) - n0
        assert fired <= 2, searches[n0:]  # debounced: not one request per key
        results.append(f"debounce: 3 keystrokes → {fired} search request(s)")
        box.fill("")
        page.wait_for_timeout(800)
        page.get_by_role("combobox", name="Ngôn ngữ quảng cáo").click()
        page.get_by_role("option", name="Tiếng Việt").click()
        cards(page)
        page.wait_for_timeout(600)
        assert page.get_by_text("Kính mát phân cực").count() >= 1 and page.get_by_text("Garden hose").count() == 0
        results.append("language filter")
        assert not errors, errors
        # second MKT, own browser context: sees its own claim as "Của bạn"
        page_b = browser.new_context(viewport={"width": 1440, "height": 1100}).new_page()
        page_b.on("pageerror", lambda e: errors.append("B: " + str(e)))
        login(page_b, "mkt-b@example.test")
        cards(page_b)
        page_b.get_by_text("Của bạn", exact=True).first.wait_for()
        page_b.screenshot(path=str(TMP / "mkt_b.png"), full_page=True)
        results.append("MKT B sees its claim as 'Của bạn'; A's claim as taken")
        assert page_b.get_by_text("MKT khác đang giữ chỗ", exact=True).count() >= 1
        assert not errors, errors
        browser.close()
    print("PASS search browser:\n  - " + "\n  - ".join(results) + f"\n  no page errors. Artifacts: {TMP}")
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
