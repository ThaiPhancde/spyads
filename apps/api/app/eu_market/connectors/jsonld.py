"""Public product page → listing via schema.org JSON-LD (Product / Offer). Spec §6.2 (4): any shop page the marketer
pastes, read exactly as the shop publishes it for search engines. No login, no CAPTCHA / anti-bot bypass: a page that
does not answer plain GET with JSON-LD gives `unsupported_capability`. robots.txt `Disallow` for `*` on the path →
`access_restricted`. The picture URL in the JSON-LD is what image search indexes (worker fetches one small thumbnail).
"""
from __future__ import annotations

import hashlib
import json
import re
from urllib.parse import urlparse

import httpx

from ...visual.service import UA, check_public_host
from .base import BAD_RESPONSE, UNAVAILABLE, UNSUPPORTED, BaseConnector, ConnectorError, listing

ACCESS_RESTRICTED = "access_restricted"
TLD_COUNTRY = {"de": "DE", "fr": "FR", "it": "IT", "es": "ES", "nl": "NL", "be": "BE", "pl": "PL", "at": "AT", "se": "SE", "dk": "DK",
               "fi": "FI", "ie": "IE", "pt": "PT", "cz": "CZ", "ro": "RO", "gr": "GR", "hu": "HU", "bg": "BG", "sk": "SK", "uk": "GB",
               "co.uk": "GB", "com": None, "eu": None}
CUR_COUNTRY = {"PLN": "PL", "SEK": "SE", "DKK": "DK", "CZK": "CZ", "RON": "RO", "HUF": "HU", "BGN": "BG", "GBP": "GB", "USD": "US", "AUD": "AU",
               "CHF": "CH", "NOK": "NO"}  # non-EU shops still count as offers; "EU" only when nothing says otherwise
MAX_HTML = 3 * 1024 * 1024
_LD = re.compile(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.S | re.I)


def country_of(url: str, currency: str | None) -> str:
    host = (urlparse(url).hostname or "").lower()
    for tld, c in sorted(TLD_COUNTRY.items(), key=lambda kv: -len(kv[0])):
        if host.endswith("." + tld) and c:
            return c
    return CUR_COUNTRY.get(currency or "", "EU")


def _walk(node):
    """Yield every dict in a JSON-LD document (handles @graph, lists, nesting)."""
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def _types(d: dict) -> set[str]:
    t = d.get("@type")
    return {x.lower() for x in (t if isinstance(t, list) else [t]) if isinstance(x, str)}


def _first(v):
    if isinstance(v, list):
        return _first(v[0]) if v else None
    if isinstance(v, dict):
        return v.get("url") or v.get("contentUrl") or v.get("name")
    return v


def parse_product(html: str, url: str) -> dict | None:
    """The first schema.org Product with an Offer on the page → normalized listing dict (None when the page has none)."""
    for m in _LD.finditer(html):
        try:
            doc = json.loads(m.group(1).strip())
        except ValueError:
            try:
                doc = json.loads(re.sub(r"[\x00-\x1f]", " ", m.group(1)))
            except ValueError:
                continue
        for d in _walk(doc):
            if "product" not in _types(d) or not d.get("name"):
                continue
            offers = d.get("offers")
            offers = offers if isinstance(offers, list) else [offers] if offers else []
            offer = next((o for o in offers if isinstance(o, dict)), {})
            if "aggregateoffer" in _types(offer):
                price = offer.get("lowPrice") or offer.get("price")
            else:
                price = offer.get("price") if offer.get("price") not in (None, "") else (offer.get("priceSpecification") or {}).get("price")
            try:
                price = float(str(price).replace(",", ".")) if price not in (None, "") else None
            except ValueError:
                price = None
            cur = offer.get("priceCurrency") or (offer.get("priceSpecification") or {}).get("priceCurrency")
            spec = offer.get("priceSpecification") if isinstance(offer.get("priceSpecification"), dict) else {}
            vat = spec.get("valueAddedTaxIncluded")
            avail = str(offer.get("availability") or "").lower()
            brand = d.get("brand")
            brand = brand.get("name") if isinstance(brand, dict) else brand
            seller = offer.get("seller")
            seller = seller.get("name") if isinstance(seller, dict) else seller
            ean = d.get("gtin13") or d.get("gtin") or d.get("gtin12") or d.get("gtin14") or d.get("ean")
            sku = d.get("sku") or d.get("productID") or d.get("mpn")
            cat = d.get("category")
            country = country_of(url, cur)
            host = urlparse(url).hostname or ""
            return listing(source="jsonld", source_product_id=f"{host}:{sku}" if sku else hashlib.sha1(url.encode()).hexdigest()[:24],
                           market_country=country, title=str(d["name"]).strip()[:512], price=price, currency=cur,
                           price_includes_vat=bool(vat) if vat is not None else None, thumbnail_url=_first(d.get("image")),
                           product_url=offer.get("url") or d.get("url") or url, seller_id=seller, seller_name=seller or host,
                           ean=str(ean) if ean else None, brand=brand if isinstance(brand, str) else None,
                           category=cat if isinstance(cat, str) else None,
                           availability="in_stock" if "instock" in avail else "out_of_stock" if "outofstock" in avail else "unknown",
                           data_access="public_page_jsonld", confidence=0.8, raw={"jsonld": {k: d.get(k) for k in ("@type", "name", "sku", "gtin13", "brand")}, "offer": offer, "page": url})
    return None


_META_TAG = re.compile("<meta[^>]*>", re.IGNORECASE)
_ATTR = re.compile("([a-zA-Z:_-]+)" + chr(92) + "s*=" + chr(92) + "s*(?:" + chr(34) + "([^" + chr(34) + "]*)" + chr(34) + "|" + chr(39) + "([^" + chr(39) + "]*)" + chr(39) + ")")


def parse_meta(html: str, url: str) -> dict | None:
    """Fallback for shops without Product JSON-LD: Open Graph / product meta tags (og:price:amount, product:price:amount,
    itemprop=price) as the shop publishes them. Needs a price AND a title; otherwise None."""
    meta: dict[str, str] = {}
    for tag in _META_TAG.findall(html[:400_000]):
        a = {k.lower(): (d if d is not None and d != "" else sq) for k, d, sq in _ATTR.findall(tag)}
        key = a.get("property") or a.get("name") or a.get("itemprop")
        if key and "content" in a:
            meta.setdefault(key.lower(), a["content"])
    amount = meta.get("product:price:amount") or meta.get("og:price:amount") or meta.get("price")
    title = meta.get("og:title") or meta.get("twitter:title")
    if not amount or not title:
        return None
    try:
        price = float(re.sub(r"[^\d.,]", "", amount).replace(",", "."))
    except ValueError:
        return None
    cur = meta.get("product:price:currency") or meta.get("og:price:currency") or meta.get("pricecurrency")
    avail = (meta.get("product:availability") or meta.get("og:availability") or "").lower()
    host = urlparse(url).hostname or ""
    return listing(source="jsonld", source_product_id=hashlib.sha1(url.encode()).hexdigest()[:24], market_country=country_of(url, cur),
                   title=title.strip()[:512], price=price, currency=cur, price_includes_vat=None, thumbnail_url=meta.get("og:image"),
                   product_url=meta.get("og:url") or url, seller_name=meta.get("og:site_name") or host, ean=meta.get("product:ean") or meta.get("og:upc"),
                   brand=meta.get("product:brand"), availability="in_stock" if "instock" in avail.replace(" ", "") else "out_of_stock" if "outofstock" in avail.replace(" ", "") else "unknown",
                   data_access="public_page_meta", confidence=0.7, raw={"meta": {k: v for k, v in meta.items() if k.startswith(("og:price", "product:", "og:title"))}, "page": url})


def robots_allows(client: httpx.Client, url: str) -> bool:
    """Minimal robots.txt: `User-agent: *` block's Disallow prefixes vs the path. Unreachable robots → allowed."""
    u = urlparse(url)
    try:
        r = client.get(f"{u.scheme}://{u.netloc}/robots.txt", timeout=8)
        if r.status_code != 200:
            return True
    except httpx.HTTPError:
        return True
    star, dis = False, []
    for line in r.text.splitlines()[:2000]:
        line = line.split("#")[0].strip()
        if not line or ":" not in line:
            continue
        k, v = [x.strip() for x in line.split(":", 1)]
        if k.lower() == "user-agent":
            star = v == "*"
        elif k.lower() == "disallow" and star and v:
            dis.append(v)
    path = u.path or "/"
    return not any(path.startswith(d.rstrip("*")) for d in dis)  # "Disallow: /" blocks everything


def fetch_product(url: str) -> dict:
    """GET one public page (SSRF-guarded, ≤ 3 MB, 15 s) and parse its Product JSON-LD. Raises ConnectorError."""
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ConnectorError(BAD_RESPONSE, "url must be http(s)")
    try:
        check_public_host(u.hostname)
    except ValueError as e:
        raise ConnectorError(ACCESS_RESTRICTED, str(e))
    with httpx.Client(follow_redirects=True, timeout=15, headers={"User-Agent": UA, "Accept": "text/html,application/xhtml+xml"}, max_redirects=3) as c:
        if not robots_allows(c, url):
            raise ConnectorError(ACCESS_RESTRICTED, "robots.txt disallows this path")
        try:
            with c.stream("GET", url) as r:
                if r.status_code in (401, 403):
                    raise ConnectorError(ACCESS_RESTRICTED, f"HTTP {r.status_code}")
                if r.status_code == 429:
                    raise ConnectorError("rate_limited", "HTTP 429")
                if r.status_code >= 400:
                    raise ConnectorError(UNAVAILABLE if r.status_code >= 500 else BAD_RESPONSE, f"HTTP {r.status_code}")
                buf = bytearray()
                for chunk in r.iter_bytes(65536):
                    buf += chunk
                    if len(buf) > MAX_HTML:
                        break
                html = bytes(buf).decode(r.encoding or "utf-8", "replace")
                final = str(r.url)
        except httpx.HTTPError as e:
            raise ConnectorError(UNAVAILABLE, type(e).__name__)
    it = parse_product(html, final)
    if not it or it.get("price") is None:  # JSON-LD without a usable Offer price → try the page's price meta tags
        it = parse_meta(html, final) or it
    if not it:
        raise ConnectorError(UNSUPPORTED, "no schema.org Product/Offer JSON-LD or price meta tags on this page")
    return it


class JsonLdConnector(BaseConnector):
    source, label, kind = "jsonld", "Public product page (JSON-LD)", "marketplace"
    countries = tuple(c for c in TLD_COUNTRY.values() if c) + ("EU",)
    env_vars = ()
    capabilities = frozenset({"get_product", "price"})
    docs_url = "https://schema.org/Product"
    notes = "Paste any public product URL: title, picture, price, currency, VAT flag, seller as the shop publishes them in JSON-LD. No search."

    def configured(self) -> bool:
        return True

    async def healthcheck(self) -> dict:
        return {"ok": True, "note": "no credentials needed"}

    async def get_product(self, source_id, country):
        """source_id = the page URL (listing.url is kept on upsert, so snapshots re-read the page)."""
        import asyncio

        return await asyncio.to_thread(fetch_product, source_id)
