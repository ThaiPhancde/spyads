"""Phase 8 browser check of the personalized Library on isolated SQLite/API/Next instances (ports 8013 / 3003).
Never touches the real database. Two MKTs: CRUD, state sync, inline similar, followed list, re-login, privacy."""
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
TMP = Path(tempfile.mkdtemp(prefix="toolspy-library-browser-"))
sys.path.insert(0, str(API))
os.environ.update(DATABASE_URL="sqlite:///" + (TMP / "test.db").as_posix(), DISABLE_SCHEDULER="1", AUTH_COOKIE_SECURE="0",
                  APP_URL="http://localhost:3003", WEB_ORIGIN="http://localhost:3003", API_URL="http://127.0.0.1:8013",
                  NEXT_DIST_DIR=".next-library-test", SMTP_HOST="", BASIC_AUTH_USER="", BASIC_AUTH_PASS="",
                  SERPAPI_API_KEY="", BRIGHTDATA_API_TOKEN="", GROK_ROUTINE_WEBHOOK_URL="", ANTHROPIC_API_KEY="",
                  GEMINI_API_KEY="", GROQ_API_KEY="", CEREBRAS_API_KEY="", CLOUDFLARE_API_TOKEN="", BRIGHTDATA_ENABLED="0")
from app.workspace.migrate import migrate  # noqa: E402
from app.workspace.auth import hash_password  # noqa: E402
from app.workspace.models import AdInteraction, Follow, Profile, User  # noqa: E402
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
    pids, ads = {}, {}
    for name, cat, lang, adv in (("Polarized sunglasses", "accessories", "en", None), ("Kính mát phân cực", "accessories", "vi", None),
                                 ("Ceramic mug", "kitchen", "en", None), ("Rival lamp", "home", "en", rival.id),
                                 ("B secret product", "toys", "en", None)):
        p = Product(product_code=f"LB-{len(pids)}", canonical_name=name, category=cat, country="PH", language=lang,
                    name_status="AUTO_CONFIRMED", in_target=True, opportunity_score=50, first_seen_at=now - timedelta(days=3),
                    last_seen_at=now, features={"unique_ads": 4, "advertiser_count": 2, "longevity_days": 20, "markets": ["PH"]})
        db.add(p); db.flush()
        a = Ad(external_id=f"lb-{p.id}", source="meta_library", platform="facebook", network="meta", product_id=p.id,
               advertiser_id=adv, title=name, ad_text=name, search_text=name.lower(), country="PH", countries=["PH"],
               language=lang, is_active=True, in_target=True, first_seen_at=now - timedelta(days=3), last_seen_at=now)
        db.add(a); db.flush(); pids[name], ads[name] = p.id, a.id
    CM.sync(db)
    A_ID, B_ID = users["mkt-a@example.test"], users["mkt-b@example.test"]
    db.add(AdInteraction(user_id=A_ID, ad_id=ads["Polarized sunglasses"], saved=True))
    db.add(AdInteraction(user_id=A_ID, ad_id=ads["Ceramic mug"], favorite=True))
    db.add(AdInteraction(user_id=B_ID, ad_id=ads["B secret product"], saved=True, note="B private note"))

for port in (8013, 3003):
    with socket.socket() as s:
        assert s.connect_ex(("127.0.0.1", port)) != 0, f"Test port {port} already occupied"
processes, logfiles = [], []
original_tsconfig = (WEB / "tsconfig.json").read_bytes()
BASE = "http://localhost:3003"


def login(page, email):
    page.goto(f"{BASE}/workspace/library/")
    page.wait_for_url("**/login/**")
    page.get_by_label("Email", exact=True).fill(email)
    page.locator("input[name='password']").fill(password)
    page.get_by_role("button", name="Đăng nhập ToolSpy", exact=True).click()
    page.wait_for_url(lambda u: "/login" not in u)
    page.goto(f"{BASE}/workspace/library/")
    page.get_by_role("heading", name="Thư viện của tôi", exact=True).wait_for()


def settle(page, ms=900):
    page.wait_for_timeout(min(ms, 500))  # 300 ms debounce …
    page.wait_for_load_state("networkidle")  # … then the request itself, however slow the machine is


def cell(page, name):
    """The grid cell (card + Library bar) of one product."""
    return page.locator("div.grid > div.flex.flex-col").filter(has_text=name).first


try:
    for name, cmd, cwd in (
        ("api", [sys.executable, "-m", "uvicorn", "app.main:app", "--port", "8013", "--host", "127.0.0.1"], API),
        ("web", ["node", str(WEB / "node_modules/next/dist/bin/next"), "dev", "-p", "3003"], WEB),
    ):
        out = (TMP / f"{name}.log").open("w", encoding="utf-8"); logfiles.append(out)
        processes.append(subprocess.Popen(cmd, cwd=cwd, stdout=out, stderr=subprocess.STDOUT,
                                          creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0))
    import httpx
    for _ in range(120):
        try:
            if httpx.get("http://127.0.0.1:8013/api/health", timeout=2).status_code == 200 and httpx.get(f"{BASE}/login/", timeout=5).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(1)
    else:
        raise RuntimeError(f"Test servers did not start; logs at {TMP}")
    results = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="msedge", headless=True)
        ctx = browser.new_context(viewport={"width": 1440, "height": 1200})
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("dialog", lambda d: d.accept())  # "Trả về kho" confirm
        login(page, "mkt-a@example.test")
        settle(page)
        assert cell(page, "Polarized sunglasses").count() == 1 and cell(page, "Ceramic mug").count() == 1
        assert page.get_by_text("B secret product").count() == 0 and page.get_by_text("B private note").count() == 0
        results.append("my library: own saved + favorite products, nothing of MKT B")
        page.screenshot(path=str(TMP / "my_library.png"), full_page=True)
        sun = cell(page, "Polarized sunglasses")
        sun.get_by_role("button", name="Bắt đầu test", exact=True).click()
        page.get_by_text("Đã bắt đầu test", exact=True).wait_for(); settle(page)
        sun = cell(page, "Polarized sunglasses")
        sun.get_by_text("Đang test", exact=True).wait_for()
        sun.get_by_role("button", name="Cập nhật kết quả test", exact=True).click()
        sun.get_by_label("Thị trường test").fill("PH")
        sun.get_by_label("Số đơn").fill("12")
        sun.get_by_role("button", name="Lưu kết quả", exact=True).click()
        page.get_by_text("Đã cập nhật kết quả test", exact=True).wait_for(); settle(page)
        cell(page, "Polarized sunglasses").get_by_text("Kết quả: Thành công", exact=True).wait_for()
        results.append("start test → Đang test → result form → Kết quả: Thành công")
        cell(page, "Polarized sunglasses").get_by_role("button", name="Trả về kho", exact=True).click()
        page.get_by_text("Đã trả sản phẩm về kho", exact=True).wait_for(); settle(page)
        sun = cell(page, "Polarized sunglasses")
        sun.get_by_text("Còn trống", exact=True).wait_for()
        assert sun.get_by_text("Kết quả: Thành công", exact=True).count() == 1
        results.append("trả về kho: ownership gone, test result kept")
        page.get_by_role("button", name="Yêu thích 1").click(); settle(page)
        assert cell(page, "Ceramic mug").count() == 1 and cell(page, "Polarized sunglasses").count() == 0
        results.append("Favorites section")
        page.get_by_role("button", name="Thư viện của tôi 2").click(); settle(page)
        sun = cell(page, "Polarized sunglasses")
        sun.get_by_role("button", name="Sản phẩm tương tự", exact=True).click()
        sun.get_by_role("link", name="Kính mát phân cực").wait_for()
        sun.locator("li").filter(has_text="Kính mát phân cực").get_by_role("button", name="Lưu", exact=True).click()
        page.get_by_text("Đã lưu vào thư viện", exact=True).wait_for(); settle(page)
        assert cell(page, "Kính mát phân cực").count() == 1
        page.screenshot(path=str(TMP / "similar_inline.png"), full_page=True)
        results.append("inline similar → save → appears in the Library")
        mug = cell(page, "Ceramic mug")
        mug.get_by_role("button", name="Ads của tôi (1)").click()
        mug.get_by_role("button", name="✓ Yêu thích", exact=True).last.click()  # the existing per-ad button
        settle(page, 1500)
        assert cell(page, "Ceramic mug").count() == 0  # unfavourited its only ad → leaves the Library at once
        results.append("per-ad button inside the card keeps the Library in sync")
        page.get_by_role("button", name="Đối thủ đang Follow").click(); settle(page)
        page.get_by_role("link", name="Rival Shop").wait_for()
        assert cell(page, "Rival lamp").count() == 1
        page.screenshot(path=str(TMP / "followed.png"), full_page=True)
        results.append("followed advertisers + their products")
        page.get_by_role("button", name="Thư viện của tôi 2").click(); settle(page)
        page.get_by_role("combobox", name="Trạng thái test").click()
        page.get_by_role("option", name="Thành công").click(); settle(page)
        assert cell(page, "Polarized sunglasses").count() == 1 and cell(page, "Kính mát phân cực").count() == 0
        results.append("test-status filter")
        before = page.request.get(f"{BASE}/api/library").json()
        page.get_by_role("button", name="Đăng xuất", exact=True).click()
        page.wait_for_url("**/login/**")
        login(page, "mkt-a@example.test"); settle(page)
        after = page.request.get(f"{BASE}/api/library").json()
        assert after["counts"] == before["counts"] and [r["id"] for r in after["rows"]] == [r["id"] for r in before["rows"]]
        assert cell(page, "Polarized sunglasses").get_by_text("Kết quả: Thành công", exact=True).count() == 1
        results.append("same Library after logout + login")
        # personal dashboard (Phase 9): real numbers, products counted as clusters, ads as ads, no Win / Rare Winner
        page.goto(f"{BASE}/workspace/"); settle(page)
        page.get_by_text("Sản phẩm của tôi", exact=False).first.wait_for()
        d = page.request.get(f"{BASE}/api/library/dashboard").json()
        assert d["kpis"]["products"]["saved"] == 2 and d["kpis"]["products"]["test_success"] == 1, d["kpis"]
        assert d["kpis"]["ads"]["saved"] == 2 and d["kpis"]["followed_advertisers"] == 1 and d["kpis"]["ads"]["new_from_followed_7d"] == 1
        body = page.inner_text("main")
        assert "1 / 0" in body and "Rival lamp" in body and "Test thành công" in body
        assert "Win" not in body.replace("Winner", "") and "Rare" not in body
        page.screenshot(path=str(TMP / "dashboard.png"), full_page=True)
        results.append("personal dashboard: real KPIs (clusters vs ads), lists, competitors' new ads, no Win/Rare")
        page_b = browser.new_context(viewport={"width": 1440, "height": 1200}).new_page()
        page_b.on("pageerror", lambda e: errors.append("B: " + str(e)))
        login(page_b, "mkt-b@example.test"); settle(page_b)
        assert cell(page_b, "B secret product").count() == 1 and cell(page_b, "Polarized sunglasses").count() == 0
        db_ = page_b.request.get(f"{BASE}/api/library/dashboard").json()
        assert db_["kpis"]["products"]["saved"] == 1 and db_["kpis"]["products"]["test_success"] == 0 and db_["new_ads"] == []
        results.append("MKT B sees only their own Library")
        assert not errors, errors
        browser.close()
    print("PASS library browser:\n  - " + "\n  - ".join(results) + f"\n  no page errors. Artifacts: {TMP}")
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
