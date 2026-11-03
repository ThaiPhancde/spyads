"""Browser worker (Meta collector upgrade, Phase 4): every Playwright task runs in its own child process.

    parent  run(task, args)  ── slot (CPU / RAM cap) ── python -m app.browser_worker <task> <in.json> <out.json>
                              ── hard deadline: kill the whole process tree (the Edge / Chrome children too)

Only the fixed tasks in TASKS run — public pages, no arbitrary URL. An access challenge (CAPTCHA, verify page, HTTP 403
on the page itself) ends the task as `blocked` and an HTTP 429 from the source as `rate_limited`: the worker reports them,
it never tries to get past them. Statuses: ok / blocked / rate_limited / timeout / busy / error.
"""
from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from .collector.base import Blocked, ConnectorError, NotConfigured, RateLimited

log = logging.getLogger(__name__)

RAM_PER_BROWSER_MB = int(os.getenv("BROWSER_RAM_MB", "700"))
TIMEOUTS = {"health": 60, "tiktok_top_ads": 240, "tiktok_video": 90, "tiktok_cc_headers": 120}  # seconds, whole task
CHANNELS = ("msedge", "chrome")  # the installed browser: no Playwright browser download on the server


# ------------------------------------------------------------ parent side
def _free_ram_mb() -> int | None:
    if os.name == "nt":
        import ctypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong), ("ullTotalPhys", ctypes.c_ulonglong),
                        ("ullAvailPhys", ctypes.c_ulonglong), ("ullTotalPageFile", ctypes.c_ulonglong),
                        ("ullAvailPageFile", ctypes.c_ulonglong), ("ullTotalVirtual", ctypes.c_ulonglong),
                        ("ullAvailVirtual", ctypes.c_ulonglong), ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        st = MEMORYSTATUSEX()
        st.dwLength = ctypes.sizeof(st)
        return st.ullAvailPhys // 2 ** 20 if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)) else None
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    except OSError:
        pass
    return None


def capacity(cpus: int | None = None, free_mb: int | None = None) -> int:
    """Browsers this process may run at once: min(BROWSER_MAX_CONCURRENCY, CPUs / 2, free RAM / BROWSER_RAM_MB), ≥ 1."""
    cpus = cpus or os.cpu_count() or 1
    free_mb = _free_ram_mb() if free_mb is None else free_mb
    cap = min(int(os.getenv("BROWSER_MAX_CONCURRENCY", "2")), max(1, cpus // 2))
    if free_mb is not None:
        cap = min(cap, free_mb // RAM_PER_BROWSER_MB)
    return max(1, cap)


# ponytail: per-process cap (API and worker each get their own); a Postgres advisory-lock pool if both browse at once
_slots: threading.BoundedSemaphore | None = None
_slots_lock = threading.Lock()


def _gate() -> threading.BoundedSemaphore:
    global _slots
    with _slots_lock:
        if _slots is None:
            _slots = threading.BoundedSemaphore(capacity())
        return _slots


def _cmd(task: str, src: Path, dst: Path) -> list[str]:
    return [sys.executable, "-m", "app.browser_worker", task, str(src), str(dst)]


def _popen(cmd: list[str]) -> subprocess.Popen:
    kw = {"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" \
        else {"start_new_session": True}
    return subprocess.Popen(cmd, cwd=Path(__file__).resolve().parent.parent, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, **kw)


def kill_tree(proc: subprocess.Popen):
    """The child and everything it started (Playwright driver → browser processes)."""
    if proc.poll() is None:
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, timeout=30)
            else:
                os.killpg(proc.pid, signal.SIGKILL)
        except Exception:
            log.exception("browser worker: could not kill process tree %s", proc.pid)
        if proc.poll() is None:
            proc.kill()
    proc.wait(timeout=30)


def run(task: str, args: dict | None = None, timeout: float | None = None, wait: float = 30) -> dict:
    """Run one browser task; returns its data. Raises Blocked / RateLimited / NotConfigured / ConnectorError."""
    if task not in TASKS:
        raise ValueError(f"unknown browser task {task}")
    timeout = timeout or TIMEOUTS.get(task, 120)
    gate = _gate()
    if not gate.acquire(timeout=wait):
        raise ConnectorError(f"BROWSER_BUSY: mọi slot trình duyệt đang bận ({task})")
    from .storage import DATA_DIR

    tmp = DATA_DIR / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f"browser-{task}-", suffix=".json", dir=tmp)
    os.close(fd)
    src, dst = Path(name), Path(name).with_suffix(".out.json")
    proc = None
    try:
        src.write_text(json.dumps(args or {}), encoding="utf-8")
        proc = _popen(_cmd(task, src, dst))
        try:
            _, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            kill_tree(proc)
            raise ConnectorError(f"BROWSER_TIMEOUT: {task} vượt {timeout:.0f} s — đã dừng trình duyệt")
        try:
            out = json.loads(dst.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            tail = (err or b"").decode(errors="replace").strip().splitlines()[-1:] or ["no output"]
            raise ConnectorError(f"BROWSER_ERROR: {task} exit {proc.returncode}: {tail[0][:200]}")
    finally:
        if proc is not None:
            kill_tree(proc)  # no-op when it already exited
        gate.release()
        src.unlink(missing_ok=True)
        dst.unlink(missing_ok=True)
    status, msg = out.get("status"), out.get("error") or ""
    if status == "ok":
        return out.get("data")
    raise {"blocked": Blocked, "rate_limited": RateLimited, "not_configured": NotConfigured}.get(status, ConnectorError)(
        f"{(status or 'error').upper()}: {msg}"[:500])


# ------------------------------------------------------------ child side
CHALLENGE = "[id*=captcha], [class*=captcha], iframe[src*=captcha], iframe[src*=challenge], #challenge-form, [id*=verify-bar]"


def challenge(page) -> str | None:
    """An access-control wall on the page (CAPTCHA, verify slider, challenge form): reported, never solved."""
    try:
        if page.locator(CHALLENGE).count():
            return "trang yêu cầu xác minh (CAPTCHA / challenge)"
    except Exception:
        pass
    return None


class Watch:
    """Responses of the source's own API calls: collect the matching JSON, notice 429s."""

    def __init__(self, page, needle: str, keep):
        self.limited = 0
        self.denied = None

        def on_response(r):
            if needle in r.url:
                if r.status == 429:
                    self.limited += 1
                elif r.status == 200:
                    try:
                        keep(r.json())
                    except Exception:
                        pass

        page.on("response", on_response)

    def check(self, page, response=None, got=None):
        if response is not None and response.status in (401, 403):
            raise Blocked(f"nguồn từ chối trang (HTTP {response.status})")
        if why := challenge(page):
            raise Blocked(why)
        if self.limited and not got:
            raise RateLimited(f"nguồn trả 429 ({self.limited} lần)")


def launch(p, profile: Path | None = None, **ctx):
    """Installed Edge, else Chrome. With `profile`: a persistent context (cookies kept), else a fresh browser."""
    err = None
    for ch in (os.getenv("BROWSER_CHANNEL"),) if os.getenv("BROWSER_CHANNEL") else CHANNELS:
        try:
            if profile is not None:
                profile.mkdir(parents=True, exist_ok=True)
                return p.chromium.launch_persistent_context(str(profile), channel=ch or None, headless=True, **ctx)
            return p.chromium.launch(channel=ch or None, headless=True)
        except Exception as e:
            err = e
    raise NotConfigured(f"cần cài Microsoft Edge hoặc Google Chrome trên máy chạy API ({err})")


def _health(p, args):
    b = launch(p)
    try:
        pg = b.new_page()
        pg.set_content("<p>ok</p>")
        return {"browser": b.version, "text": pg.inner_text("p")}
    finally:
        b.close()


def _tiktok_top_ads(p, args):
    from .collector.adapters.ad_libraries import TikTokTopAdsHeadlessConnector as T

    seen: list[dict] = []
    b = launch(p)
    try:
        pg = b.new_context(locale="en-US", viewport={"width": 1400, "height": 900}, user_agent=T.UA).new_page()
        w = Watch(pg, "/top_ads/v2/list", seen.append)
        resp = None
        try:
            resp = pg.goto(T.URL.format(period=args["period"], country=args["country"]), wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            log.warning("tiktok_top_ads %s: %s", args["country"], str(e).splitlines()[0])
        pg.wait_for_timeout(8000)
        w.check(pg, resp, seen)
        pg.keyboard.press("Escape")  # promo popup covers the page
        for _ in range(8):  # the list pages with a "View More" button; the login wall ends it
            btn = pg.get_by_text("View More", exact=False)
            if not btn.count():
                break
            before = len(seen)
            try:
                btn.first.click(timeout=8000)
            except Exception:  # promo modal re-opened over the button: close it, retry once, keep what we have
                pg.keyboard.press("Escape")
                try:
                    btn.first.click(timeout=8000)
                except Exception:
                    break
            pg.wait_for_timeout(3500)
            if len(seen) == before:
                break
        w.check(pg, None, seen)
    finally:
        b.close()
    return seen


def _tiktok_video(p, args):
    from .collector.adapters.ad_libraries import TikTokTopAdsHeadlessConnector as T

    got: dict = {}
    b = launch(p)
    try:
        pg = b.new_context(locale="en-US", user_agent=T.UA).new_page()
        w = Watch(pg, "/top_ads/v2/detail", lambda j: got.update((j.get("data") or {}).get("video_info") or {}))
        resp = pg.goto(f"https://ads.tiktok.com/business/creativecenter/topads/{args['material_id']}/pc/en",
                       wait_until="domcontentloaded", timeout=60000)
        for _ in range(30):
            if got:
                break
            pg.wait_for_timeout(500)
        w.check(pg, resp, got)
    finally:
        b.close()
    return got


def _tiktok_cc_headers(p, args):
    from .collector.adapters.ad_libraries import CC_PAGE, TIKTOK_PROFILE, TikTokTopAdsHeadlessConnector as T

    got: dict = {}

    def on_request(r):
        if "/top_ads/v2/list" in r.url and not got:
            got.update({k: v for k, v in r.all_headers().items()
                        if not k.startswith(":") and k not in ("host", "content-length", "accept-encoding")})

    ctx = launch(p, TIKTOK_PROFILE, locale="en-US", user_agent=T.UA)
    try:
        pg = ctx.new_page()
        pg.on("request", on_request)
        w = Watch(pg, "/top_ads/v2/list", lambda j: None)
        resp = pg.goto(CC_PAGE, wait_until="domcontentloaded", timeout=60000)
        for _ in range(40):
            if got:
                break
            pg.wait_for_timeout(500)
        w.check(pg, resp, got)
    finally:
        ctx.close()
    return got


TASKS = {"health": _health, "tiktok_top_ads": _tiktok_top_ads, "tiktok_video": _tiktok_video, "tiktok_cc_headers": _tiktok_cc_headers}


def main(task: str, src: str, dst: str) -> int:
    from playwright.sync_api import sync_playwright

    t0 = time.perf_counter()
    try:
        with sync_playwright() as p:
            out = {"status": "ok", "data": TASKS[task](p, json.loads(Path(src).read_text(encoding="utf-8")))}
    except Blocked as e:
        out = {"status": "blocked", "error": str(e)}
    except RateLimited as e:
        out = {"status": "rate_limited", "error": str(e)}
    except NotConfigured as e:
        out = {"status": "not_configured", "error": str(e)}
    except Exception as e:  # noqa: BLE001 — reported to the parent, never a traceback into the page
        out = {"status": "error", "error": f"{type(e).__name__}: {str(e).splitlines()[0] if str(e) else ''}"[:300]}
    out["ms"] = int((time.perf_counter() - t0) * 1000)
    Path(dst).write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:4]))
