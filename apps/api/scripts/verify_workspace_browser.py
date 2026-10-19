"""Browser integration against isolated SQLite/API/Next instances. No production users or mock Ads are inserted."""
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[3]
API = ROOT / "apps/api"
WEB = ROOT / "apps/web"
TMP = Path(tempfile.mkdtemp(prefix="toolspy-browser-"))
sys.path.insert(0, str(API))
os.environ.update(DATABASE_URL="sqlite:///" + (TMP / "test.db").as_posix(), DISABLE_SCHEDULER="1", AUTH_COOKIE_SECURE="0",
                  APP_URL="http://localhost:3001", WEB_ORIGIN="http://localhost:3001", API_URL="http://127.0.0.1:8011",
                  NEXT_DIST_DIR=".next-workspace-test", SMTP_HOST="", BASIC_AUTH_USER="", BASIC_AUTH_PASS="",
                  SERPAPI_API_KEY="", BRIGHTDATA_API_TOKEN="", GROK_ROUTINE_WEBHOOK_URL="", ANTHROPIC_API_KEY="",
                  GEMINI_API_KEY="", GROQ_API_KEY="", CEREBRAS_API_KEY="", CLOUDFLARE_API_TOKEN="", BRIGHTDATA_ENABLED="0")
from app.workspace.migrate import migrate
from app.workspace.auth import hash_password
from app.workspace.models import Profile, User
from app.db import SessionLocal
from app.models import Ad, Advertiser, Product
from playwright.sync_api import sync_playwright

migrate()
password = "Browser integration password 123!"
with SessionLocal.begin() as db:
    u = User(name="Browser MKT", email="browser@example.test", password_hash=hash_password(password), role="MKT", status="active")
    db.add(u); db.flush(); db.add(Profile(user_id=u.id))
    adv = Advertiser(name="Browser advertiser fixture", source="meta_library", source_advertiser_id="100", platform="facebook", country="PH")
    db.add(adv); db.flush(); aid = adv.id
    p = Product(product_code="BROWSER_TEST", canonical_name="lucky bracelet", country="PH", name_status="VERIFIED")
    db.add(p); db.flush()
    db.add(Ad(external_id="browser-fixture", source="meta_library", platform="facebook", advertiser_id=aid,
              product_id=p.id, ad_text="lucky bracelet", title="lucky bracelet", search_text="lucky bracelet", country="PH", page_id="100", network="meta", channel="ads"))

for port in (8011, 3001):
    with socket.socket() as s:
        assert s.connect_ex(("127.0.0.1", port)) != 0, f"Test port {port} already occupied"
processes = []
logfiles = []
original_tsconfig = (WEB / "tsconfig.json").read_bytes()
try:
    for name, cmd, cwd in (
        ("api", [sys.executable, "-m", "uvicorn", "app.main:app", "--port", "8011", "--host", "127.0.0.1"], API),
        ("web", ["node", str(WEB / "node_modules/next/dist/bin/next"), "dev", "-p", "3001"], WEB),
    ):
        out = (TMP / f"{name}.log").open("w", encoding="utf-8"); logfiles.append(out)
        processes.append(subprocess.Popen(cmd, cwd=cwd, stdout=out, stderr=subprocess.STDOUT,
                                          creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0))
    import httpx
    for _ in range(60):
        try:
            if httpx.get("http://127.0.0.1:8011/api/health", timeout=2).status_code == 200 and httpx.get("http://localhost:3001/login/", timeout=3).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(1)
    else:
        raise RuntimeError(f"Test servers did not start; logs at {TMP}")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto("http://localhost:3001/workspace/")
        page.wait_for_url("**/login/")
        page.get_by_label("Email", exact=True).fill("browser@example.test")
        page.locator("input[name='password']").fill(password)
        page.get_by_role("button", name="Đăng nhập ToolSpy", exact=True).click()
        page.wait_for_url("**/workspace/")
        page.get_by_text("Không gian của Browser MKT", exact=True).wait_for()
        page.goto(f"http://localhost:3001/competitors/{aid}/")
        page.get_by_role("button", name="+ Follow đối thủ", exact=True).click()
        page.get_by_role("button", name="Đang theo dõi · Bỏ theo dõi", exact=True).wait_for()
        page.get_by_role("button", name="Lịch sử hoạt động", exact=True).click()
        page.get_by_text("Bắt đầu theo dõi", exact=True).wait_for()
        page.get_by_role("button", name="Đăng xuất", exact=True).click()
        page.wait_for_url("**/login/")
        page.get_by_label("Email", exact=True).fill("browser@example.test")
        page.locator("input[name='password']").fill(password)
        page.get_by_role("button", name="Đăng nhập ToolSpy", exact=True).click()
        page.wait_for_url("**/workspace/")
        page.goto("http://localhost:3001/competitors/")
        page.get_by_role("link", name="Browser advertiser fixture", exact=True).wait_for()
        page.screenshot(path=str(TMP / "competitors.png"), full_page=True)
        page.goto("http://localhost:3001/workspace/filters/")
        page.get_by_role("button", name="Whitelist", exact=True).click()
        page.get_by_text("Browser advertiser fixture · whitelist", exact=True).wait_for()
        page.get_by_label("Advertiser", exact=True).select_option("whitelist")
        page.goto("http://localhost:3001/ads/")
        page.get_by_role("button", name="Lưu", exact=True).first.click()
        page.get_by_role("button", name="✓ Lưu", exact=True).first.wait_for()
        page.goto("http://localhost:3001/workspace/library/")
        page.get_by_role("heading", name="Thư viện của tôi", exact=True).wait_for()
        page.get_by_text("lucky bracelet", exact=True).first.wait_for()
        page.goto("http://localhost:3001/search/?saved_filter=test&q=lucky%20bracelet&country=PH&has_video=true&min_days=7&max_advertisers=3&min_networks=2&name_status=VERIFIED&expand=false")
        page.get_by_role("button", name="Lưu bộ lọc hiện tại", exact=True).click()
        page.get_by_text("Đã lưu bộ lọc vào tài khoản", exact=True).wait_for()
        page.goto("http://localhost:3001/workspace/searches/")
        page.get_by_role("link", name="lucky bracelet", exact=True).click()
        page.wait_for_url(lambda url: "/search?" in url or "/search/?" in url)
        page.get_by_role("button", name="Lưu bộ lọc hiện tại", exact=True).click()
        page.get_by_text("Đã lưu bộ lọc vào tài khoản", exact=True).wait_for()
        restored = page.request.get("http://localhost:3001/api/workspace/searches").json()["rows"][0]["filters"]
        assert all(restored.get(k) == v for k, v in {"q": "lucky bracelet", "country": "PH", "funnel": "", "has_video": "true", "min_days": "7", "max_advertisers": "3", "min_networks": "2", "name_status": "VERIFIED", "expand": "false"}.items()), restored
        page.goto("http://localhost:3001/account/")
        page.get_by_role("button", name="Lưu hồ sơ", exact=True).wait_for()
        page.get_by_role("button", name="Đăng xuất", exact=True).click()
        page.wait_for_url("**/login/")
        page.goto("http://localhost:3001/register/")
        page.get_by_label("Tên", exact=True).fill("New MKT")
        page.get_by_label("Email", exact=True).fill("new-mkt@example.test")
        page.locator("input[name='password']").fill(password)
        page.get_by_role("button", name="Tạo tài khoản MKT", exact=True).click()
        page.get_by_role("status").filter(has_text="Đăng ký thành công").wait_for()
        page.goto("http://localhost:3001/login/")  # no approval step any more: log in right away
        page.get_by_label("Email", exact=True).fill("new-mkt@example.test")
        page.locator("input[name='password']").fill(password)
        page.get_by_role("button", name="Đăng nhập ToolSpy", exact=True).click()
        page.wait_for_url("**/workspace/")
        page.get_by_text("Không gian của New MKT", exact=True).wait_for()
        assert page.get_by_role("link", name="Quản lý tài khoản").count() == 0  # no ADMIN screens
        assert page.request.get("http://localhost:3001/api/admin/users").status == 403
        assert not errors, errors
        browser.close()
    print(f"PASS browser: registration (active at once, no approval), login, redirect, Follow, timeline, persistence, whitelist, saved Ads, saved search restoration, profile, no ADMIN screens; no page errors. Artifacts: {TMP}")
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
