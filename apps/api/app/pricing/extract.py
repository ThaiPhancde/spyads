"""Direct price extraction from one product page — the free step every ladder tries before a paid provider.

One GET (SSRF-guarded, robots.txt respected, ≤3 MB) → JSON-LD Product/Offer (eu_market.connectors.jsonld), else the
landing-page parser (services.spy: og:price, itemprop, DOM price class), else Shopify /products/<handle>.json.
A page that cannot be read says why (code) so the router can decide whether Bright Data is worth a request.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx

from ..eu_market.connectors import jsonld
from ..ingest import parse_price
from ..services import spy
from ..visual.service import UA, check_public_host


class ExtractError(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code, self.detail = code, detail


def _fetch(url: str) -> tuple[str, str]:
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ExtractError("bad_url")
    try:
        check_public_host(u.hostname)
    except ValueError as e:
        raise ExtractError("access_restricted", str(e))
    with httpx.Client(follow_redirects=True, timeout=15, headers={"User-Agent": UA, "Accept": "text/html,application/xhtml+xml"}, max_redirects=4) as c:
        if not jsonld.robots_allows(c, url):
            raise ExtractError("access_restricted", "robots.txt")
        try:
            r = c.get(url)
        except httpx.HTTPError as e:
            raise ExtractError("temporarily_unavailable", type(e).__name__)
    if r.status_code in (401, 403):
        raise ExtractError("blocked", f"HTTP {r.status_code}")
    if r.status_code == 404:
        raise ExtractError("not_found", "HTTP 404")
    if r.status_code == 429:
        raise ExtractError("rate_limited", "HTTP 429")
    if r.status_code >= 400:
        raise ExtractError("bad_response", f"HTTP {r.status_code}")
    final = str(r.url)
    if urlparse(final).hostname != u.hostname:
        try:
            check_public_host(urlparse(final).hostname or "")
        except ValueError:
            raise ExtractError("access_restricted", "redirect to private host")
    return r.text[:3 * 1024 * 1024], final


def parse_html(html: str, url: str) -> dict | None:
    """→ {price, currency, original_price, title, seller, method, confidence, evidence, availability} or None."""
    it = jsonld.parse_product(html, url)
    if it and it.get("price") is not None:
        return {"price": it["price"], "currency": it.get("currency"), "original_price": None, "title": it.get("title"), "seller": it.get("seller_name"),
                "method": "jsonld", "confidence": 0.9, "availability": it.get("availability"), "ean": it.get("ean"), "brand": it.get("brand"),
                "sku": ((it.get("raw") or {}).get("jsonld") or {}).get("sku"),
                "evidence": {"offer": (it.get("raw") or {}).get("offer")}}
    meta = jsonld.parse_meta(html, url)
    if meta and meta.get("price") is not None:
        return {"price": meta["price"], "currency": meta.get("currency"), "original_price": None, "title": meta.get("title"), "seller": meta.get("seller_name"),
                "method": "meta", "confidence": 0.8, "availability": meta.get("availability"), "evidence": (meta.get("raw") or {}).get("meta")}
    land = spy.parse_landing(html, url)
    if land.get("price") is not None:
        return {"price": land["price"], "currency": land.get("currency"), "original_price": land.get("compare_at"), "title": None, "seller": None,
                "method": "dom", "confidence": 0.6 if land.get("currency") else 0.4, "availability": None, "evidence": {"parser": "spy.parse_landing"}}
    return None


def shopify_json(url: str) -> dict | None:
    """Shopify stores publish /products/<handle>.json — the variants' prices without any scraping."""
    u = urlparse(url)
    if "/products/" not in u.path:
        return None
    path = re.sub(r"\.json$", "", u.path.rstrip("/")) + ".json"
    try:
        r = httpx.get(f"{u.scheme}://{u.netloc}{path}", timeout=12, headers={"User-Agent": UA}, follow_redirects=True)
        if r.status_code != 200 or "json" not in r.headers.get("content-type", ""):
            return None
        j = r.json()
        p = (j.get("product") if isinstance(j, dict) else None) or {}
    except (httpx.HTTPError, ValueError):
        return None
    vs = [(spy._num(v.get("price")), spy._num(v.get("compare_at_price")), v.get("title")) for v in p.get("variants") or []]
    vs = [v for v in vs if v[0]]
    if not vs:
        return None
    lo = min(vs, key=lambda v: v[0])
    return {"price": lo[0], "currency": None, "original_price": lo[1], "title": p.get("title"), "seller": u.hostname, "method": "shopify_json", "confidence": 0.85,
            "availability": None, "evidence": {"variants": len(vs), "variant": lo[2], "price_high": max(v[0] for v in vs)}}


def extract(url: str) -> dict:
    """Direct read of one product page. Raises ExtractError(code) — `blocked` / `access_restricted` are what the router
    may hand to Bright Data; `no_price` / `not_found` are final."""
    html, final = _fetch(url)
    got = parse_html(html, final) or shopify_json(final)
    if not got:
        raise ExtractError("no_price", "no JSON-LD / meta / DOM price on the page")
    if got.get("currency") is None:
        m = re.search(r'Shopify\.currency\s*=\s*\{"active":"([A-Z]{3})"', html) or re.search(r'"currency"\s*:\s*"([A-Z]{3})"', html)
        got["currency"] = m.group(1) if m else None
    got["url"] = final
    got["seller"] = got.get("seller") or (urlparse(final).hostname or "").removeprefix("www.")
    return got


def markdown_price(md: str, country: str | None) -> dict | None:
    """Price from a page Bright Data rendered as Markdown: the first explicit-currency amount in the product area
    (after the first heading, before any 'related products' heading). ponytail: naive text heuristic — a combo /
    shipping amount can win; stored as a reference (confidence 0.5), never as verified."""
    body = md.split("\n# ", 1)[-1] if "\n# " in md else md
    body = re.split(r"\n#+\s*(?:related|you may also|recommended|similar|customers also)", body, 1, flags=re.I)[0][:6000]
    pc = parse_price(body, country)
    if not pc or not pc[1]:
        return None
    at = body.find(str(int(pc[0])))
    return {"price": pc[0], "currency": pc[1], "original_price": None, "title": None, "seller": None, "method": "markdown", "confidence": 0.5,
            "availability": None, "evidence": {"excerpt": body[max(0, at - 80):at + 120]}}
