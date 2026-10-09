"""Prototype: read TikTok Creative Center Top Ads WITHOUT a login, by letting a real (headless) browser load the public
page; TikTok's own JS signs the request, the app only listens to the answer. Nothing is forged or replayed.

    .venv/Scripts/python headless_top_ads.py [PH] [--show]      (--show = visible window, to see captcha / login walls)

Needs Edge or Chrome installed (channel msedge / chrome); no browser download.
"""
import json
import sys

from playwright.sync_api import sync_playwright

country = next((a.upper() for a in sys.argv[1:] if not a.startswith("-")), "PH")
period = next((a.split("=")[1] for a in sys.argv[1:] if a.startswith("--period=")), "30")  # 7 / 30 / 180
URL = f"https://ads.tiktok.com/business/creativecenter/inspiration/topads/pc/en?period={period}&region={country}"
seen: list[dict] = []


def on_response(r):
    if "/top_ads/v2/list" in r.url and r.status == 200:
        try:
            seen.append(r.json())
        except Exception:
            pass


with sync_playwright() as p:
    for ch in ("msedge", "chrome"):
        try:
            b = p.chromium.launch(channel=ch, headless="--show" not in sys.argv)
            break
        except Exception as e:
            err = e
    else:
        sys.exit(f"no Edge/Chrome found: {err}")
    UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36 Edg/154.0.0.0"  # default headless UA says "HeadlessChrome"
    ctx = b.new_context(locale="en-US", viewport={"width": 1400, "height": 900}, user_agent=UA)
    pg = ctx.new_page()
    pg.on("response", on_response)
    pg.on("response", lambda r: r.url == URL and print("main document → HTTP", r.status, r.headers.get("x-tt-logid", ""), r.headers.get("server", "")))
    try:
        pg.goto(URL, wait_until="domcontentloaded", timeout=60000)
    except Exception as e:  # blocked page: keep going to report what the browser saw
        print("goto:", str(e).splitlines()[0])
    pg.wait_for_timeout(8000)
    for _ in range(6):  # lazy list: scroll to load more pages
        pg.mouse.wheel(0, 4000)
        pg.wait_for_timeout(2500)
    pg.keyboard.press("Escape")  # promo popup (CreativeCenterRevampPopup) covers the page
    pg.wait_for_timeout(800)
    for _ in range(8):  # the list pages with a "View more" button, not infinite scroll
        btn = pg.get_by_text("View More", exact=False)
        if not btn.count():
            break
        before = len(seen)
        btn.first.click(timeout=8000)
        pg.wait_for_timeout(3500)
        if len(seen) == before:  # click opened a login dialog instead of a page
            print("View More → no new page (login prompt?)", pg.locator("[role=dialog], .login, [class*=login]").count())
            break
    print("page title:", pg.title(), "| url:", pg.url)
    print("login wall:", pg.locator("text=/log in|sign in/i").count() > 0)
    b.close()

mats = [m for d in seen for m in ((d.get("data") or {}).get("materials") or [])]
print(f"responses={len(seen)} code={[d.get('code') for d in seen]} ads={len(mats)} unique={len({m['id'] for m in mats})}")
for m in mats[:3]:
    print(m.get("id"), m.get("brand_name"), m.get("ad_title"), m.get("objective_key"), (m.get("video_info") or {}).get("vid"))
json.dump(seen, open("headless_top_ads_sample.json", "w"))
