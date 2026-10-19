"""Bright Data Web MCP (https://docs.brightdata.com/ai/mcp-server/overview) over streamable HTTP — no SDK.

Tools verified live on 2026-10-11 with this account: search_engine, search_engine_batch, scrape_as_markdown, scrape_batch,
search_scrapers / run_scraper (Web Scraper API datasets — a different, paid product: never called unless
BRIGHTDATA_STRUCTURED_ENABLED). No reverse-image search exists here — that stays SerpApi Lens.
Every call = 1 request on the 5,000/month free tier (local ledger: provider "brightdata"); batch tools are not used
because their billing per query is not documented as one credit.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time

import httpx

from . import quota

log = logging.getLogger("app.pricing.brightdata")
MCP_URL = "https://mcp.brightdata.com/mcp"
PROVIDER = quota.BRIGHTDATA
_SNIPPET_PRICE = re.compile(r"(?:from\s+)?(₱|€|£|US\$|A\$|AED|SAR|PHP|EUR|USD|GBP|PLN|zł|\$)\s?(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?)\s?(PHP|EUR|USD|GBP|AED|SAR|PLN|AUD)?", re.I)
_SYM = {"₱": "PHP", "€": "EUR", "£": "GBP", "US$": "USD", "A$": "AUD", "ZŁ": "PLN"}


def _flag(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).split("#")[0].strip().lower() in ("1", "true", "yes", "on")


def token() -> str | None:
    return (os.getenv("BRIGHTDATA_API_TOKEN") or "").strip() or None


def unavailable(capability: str = "search") -> str | None:
    if not _flag("BRIGHTDATA_ENABLED"):
        return "brightdata_disabled"
    if not token():
        return "missing_api_token"
    if capability == "search" and not _flag("BRIGHTDATA_SEARCH_ENABLED", "true"):
        return "search_disabled"
    if capability == "scrape" and not _flag("BRIGHTDATA_SCRAPE_ENABLED", "true"):
        return "scrape_disabled"
    if capability == "structured" and not _flag("BRIGHTDATA_STRUCTURED_ENABLED"):
        return "structured_disabled"
    return None


class BrightDataError(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code, self.detail = code, detail


# ------------------------------------------------------------------ MCP session (one per process, re-made on 404)
_session = {"id": None}
_session_lock = threading.Lock()


def _headers() -> dict:
    h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if _session["id"]:
        h["Mcp-Session-Id"] = _session["id"]
    return h


def _url() -> str:
    return f"{MCP_URL}?token={token()}"


def _parse(text: str) -> dict:
    for line in text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[5:])
    return json.loads(text) if text.strip().startswith("{") else {}


def _init(c: httpx.Client):
    r = c.post(_url(), headers=_headers(), json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "spyads", "version": "1"}}})
    if r.status_code != 200 or not r.headers.get("mcp-session-id"):
        raise BrightDataError("unauthorized" if r.status_code in (401, 403) else "provider_error", f"initialize HTTP {r.status_code}")
    _session["id"] = r.headers["mcp-session-id"]
    c.post(_url(), headers=_headers(), json={"jsonrpc": "2.0", "method": "notifications/initialized"})


def tool_call(name: str, arguments: dict, timeout: float = 90) -> str:
    """One MCP tools/call → the text content. Budget is reserved BEFORE any network (initialize included), so an
    exhausted budget never touches the provider. Counts one request; a session that expired (404) is re-made once."""
    floor = quota.env_int("BRIGHTDATA_RESERVE", 200)
    body = {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": name, "arguments": arguments}}
    cid = quota.reserve(PROVIDER, name, 1, floor)
    t0 = time.time()
    with _session_lock, httpx.Client(timeout=timeout) as c:
        try:
            if not _session["id"]:
                _init(c)
            r = c.post(_url(), headers=_headers(), json=body)
            if r.status_code == 404:
                _session["id"] = None
                _init(c)
                r = c.post(_url(), headers=_headers(), json=body)
        except BrightDataError as e:
            quota.finish(cid, False, None, t0, e.code, counted=False)
            raise
        except httpx.ConnectError as e:
            quota.finish(cid, False, None, t0, "network_error", counted=False)
            raise BrightDataError("network_error", str(e)[:120])
        except httpx.TimeoutException:
            quota.finish(cid, False, None, t0, "timeout")  # may still have been charged
            raise BrightDataError("timeout", "Bright Data did not answer in time")
    if r.status_code != 200:
        code = "unauthorized" if r.status_code in (401, 403) else "rate_limited" if r.status_code == 429 else "provider_error"
        quota.finish(cid, False, r.status_code, t0, code, counted=False if r.status_code in (401, 403) else None)
        raise BrightDataError(code, f"HTTP {r.status_code}")
    j = _parse(r.text)
    if j.get("error"):
        quota.finish(cid, False, 200, t0, "provider_error")
        raise BrightDataError("provider_error", str(j["error"])[:200])
    res = j.get("result") or {}
    text = "".join(c.get("text", "") for c in res.get("content", []) if c.get("type") == "text")
    if res.get("isError"):
        quota.finish(cid, False, 200, t0, "tool_error")
        raise BrightDataError("tool_error", text[:200])
    quota.finish(cid, True, 200, t0)
    return text


def strip_untrusted(text: str) -> str:
    """The server wraps fetched content in =====UNTRUSTED_<id>_BEGIN/END===== markers: keep only the data inside."""
    m = re.search(r"=====UNTRUSTED_[0-9a-f]+_BEGIN=====\s*(.*?)\s*(?:=====UNTRUSTED_[0-9a-f]+_END=====|$)", text, re.S)
    return m.group(1) if m else text


def snippet_prices(text: str, country: str | None = None) -> list[tuple[float, str]]:
    """Money amounts with an explicit currency in a SERP snippet ("From ₱1,900.00 PHP"). A bare "$" is only a price
    when the search country says which dollar it is."""
    dollar = {"US": "USD", "AU": "AUD", "CA": "CAD", "NZ": "NZD", "SG": "SGD"}.get((country or "").upper())
    out = []
    for sym, num, iso in _SNIPPET_PRICE.findall(text or ""):
        cur = iso or _SYM.get(sym.upper()) or (dollar if sym == "$" else sym)
        if not cur or cur == "$":
            continue
        cur = cur.upper()
        parts = re.split(r"[.,]", num)
        dec = parts.pop() if len(parts) > 1 and len(parts[-1]) != 3 else None
        try:
            out.append((float("".join(parts) + (f".{dec}" if dec else "")), cur))
        except ValueError:
            pass
    return out


_geo_bad: dict[str, float] = {}  # geo_location → until when to skip it (in-process; the worker is one process)


def _serp(query: str, geo: str | None) -> dict:
    args = {"query": query, "engine": "google"}
    if geo:
        args["geo_location"] = geo
    text = strip_untrusted(tool_call("search_engine", args, timeout=float(os.getenv("BRIGHTDATA_SEARCH_TIMEOUT_S", "45"))))  # measured 8-70 s: bounded so "Hiện giá" answers inside the web proxy window
    try:
        j = json.loads(text)
    except ValueError:
        raise BrightDataError("bad_response", "search_engine did not return JSON")
    return j if isinstance(j, dict) else {"organic": j if isinstance(j, list) else []}


def search(query: str, country: str | None = None) -> list[dict]:
    """Google SERP via search_engine → [{link, title, description, prices:[(amount, cur)]}]. 1 request."""
    why = unavailable("search")
    if why:
        raise BrightDataError(why)
    geo = country.upper() if country and len(country) == 2 and country.upper() != "WW" else None
    if geo and _geo_bad.get(geo, 0) > time.time():
        geo = None  # this geo answered empty recently: go straight to the global SERP (one request, not two)
    j = _serp(query, geo)
    if geo and not j.get("organic"):  # measured 2026-10-11: geo US / GB → {"organic": []} after ~50 s, PH fine, no geo → 9 results in 6 s
        _geo_bad[geo] = time.time() + 6 * 3600
        log.info("brightdata search: geo %s empty, retrying without geo", geo)
        j = _serp(query, None)
    out = []
    for it in (j.get("organic") or [])[:20]:
        if not str(it.get("link") or "").startswith("http"):
            continue  # relative /goto?url=… redirect entries are not pages
        out.append({"link": it["link"], "title": it.get("title"), "description": it.get("description"),
                    "prices": snippet_prices(f"{it.get('title') or ''} {it.get('description') or ''}", country)})
    return out


def scrape_markdown(url: str) -> str:
    """scrape_as_markdown: the public page as Markdown (Bright Data fetches it; we never bypass a login). 1 request."""
    why = unavailable("scrape")
    if why:
        raise BrightDataError(why)
    return strip_untrusted(tool_call("scrape_as_markdown", {"url": url}, timeout=120))
