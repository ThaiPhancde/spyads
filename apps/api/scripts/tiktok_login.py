"""One-time login for the TikTok Creative Center connector (tiktok_top_ads).

Opens a real Edge/Chrome window on the profile the connector harvests its headers from (data/browser/tiktok).
Log in to ads.tiktok.com with the free account, then close the window. From then on every run reads fresh
cookies + signed headers from that profile on its own; repeat only if the connector reports AUTH_EXPIRED.

    cd apps/api && .venv/Scripts/python.exe scripts/tiktok_login.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.sync_api import sync_playwright  # noqa: E402

from app.collector.adapters.ad_libraries import TIKTOK_PROFILE, auto_cc_headers  # noqa: E402

TIKTOK_PROFILE.mkdir(parents=True, exist_ok=True)
with sync_playwright() as p:
    ctx = None
    for ch in ("msedge", "chrome"):
        try:
            ctx = p.chromium.launch_persistent_context(str(TIKTOK_PROFILE), channel=ch, headless=False, locale="en-US",
                                                       no_viewport=True)
            break
        except Exception as e:
            print(f"{ch}: {e}")
    if ctx is None:
        sys.exit("Cần Microsoft Edge hoặc Google Chrome")
    pg = ctx.pages[0] if ctx.pages else ctx.new_page()
    pg.goto("https://ads.tiktok.com/business/creativecenter/inspiration/topads/pc/en")
    print("Đăng nhập TikTok trong cửa sổ vừa mở, xong thì ĐÓNG cửa sổ…")
    pg.wait_for_event("close", timeout=0)
    ctx.close()

h = auto_cc_headers(fresh=True)
print("OK — đã đăng nhập, connector sẽ tự thu headers" if "sessionid" in h.get("cookie", "")
      else "Chưa thấy phiên đăng nhập (sessionid) — chạy lại và đăng nhập")
