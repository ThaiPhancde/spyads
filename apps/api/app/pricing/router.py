"""SmartPriceProviderRouter — the ladder one subject climbs until it has a price worth showing (spec §IV).

Order is cheapest-first and stops at the first rung that holds:
  memory → what the ad already carries (copy / landing) → direct page read (free) → Bright Data scrape of a page that
  blocked us → cluster reference from memory → Bright Data search (+ direct read of the top shop link) → SerpApi
  Shopping when Bright Data cannot → NOT_FOUND (negative cache).
A verified price always names the seller + page; a reference is a range from search results, shared by every ad of
the same product cluster. Nothing here guesses: no price with an unknown currency is ever stored.
"""
from __future__ import annotations

import logging
import re
import statistics
import time
from datetime import datetime, timedelta
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..eu_market.models import EUListing
from ..markets import known_country
from ..models import Ad, Advertiser, Product
from ..services.spy import SKIP_HOST
from . import brightdata, extract, shopping
from .models import MARKET_REFERENCE, NOT_FOUND, VERIFIED_COMPETITOR, VERIFIED_MARKETPLACE, PriceObservation
from .quota import QuotaError, env_int

log = logging.getLogger("app.pricing")
TTL_H = {VERIFIED_COMPETITOR: 7 * 24, VERIFIED_MARKETPLACE: 7 * 24, MARKET_REFERENCE: 14 * 24, NOT_FOUND: 3 * 24}
VERIFIED_MIN_CONF = 0.7  # below this a page read is stored as a reference, not a verified price
# hosts whose organic result is never a product page we can read a price from
NOT_A_SHOP = re.compile(r"facebook\.|instagram\.|tiktok\.|youtube\.|youtu\.be|pinterest\.|reddit\.|wikipedia\.|quora\.|twitter\.|x\.com|"
                        r"linkedin\.|amazon\.|shopee\.|lazada\.|temu\.|alibaba\.|aliexpress\.|ebay\.|etsy\.|google\.", re.I)
MARKET_CURRENCY = {"PH": "PHP", "US": "USD", "GB": "GBP", "AU": "AUD", "NZ": "NZD", "SA": "SAR", "AE": "AED", "KW": "KWD", "QA": "QAR", "OM": "OMR",
                   "BH": "BHD", "JO": "JOD", "EG": "EGP", "IQ": "IQD", "VN": "VND", "PL": "PLN", "SE": "SEK", "DK": "DKK", "CZ": "CZK", "RO": "RON",
                   **{c: "EUR" for c in ("DE", "FR", "IT", "ES", "NL", "BE", "AT", "FI", "IE", "PT", "GR")}}


def coherent_range(amts: list[float]) -> tuple[float, float, float] | None:
    """(median, low, high) of the middle half of the amounts, or None when they do not describe one product
    (spread > 6× after trimming = mixed products / combos / wrong currency → anomaly, nothing stored)."""
    a = sorted(amts)
    if len(a) >= 4:
        a = a[len(a) // 4: -(len(a) // 4) or None]
    if not a or a[0] <= 0 or a[-1] / a[0] > 6:
        return None
    return a[len(a) // 2], a[0], a[-1]


def plausible(price: float | None, currency: str | None, market: str | None) -> str | None:
    """Anomaly gate for a stored / parsed price (spec §13.4): None = fine, else the reason. A consumer product sells for
    ~$0.5–$5,000; a currency that is neither the market's nor a world currency was mis-parsed (VND on a US ad)."""
    from ..services.enrichment import FX_TO_USD

    if price is None or not currency:
        return "no_currency"
    cur = currency.upper()
    if market and market in MARKET_CURRENCY and cur not in (MARKET_CURRENCY[market], "USD", "EUR", "GBP"):
        return "currency_market_mismatch"
    usd = price * FX_TO_USD.get(cur, 1)
    if not 0.5 <= usd <= 5000:
        return "out_of_range"
    return None


class Step:
    """Collects {step, ok, code, ms} rows — the job's audit trail (why a price is / isn't there)."""

    def __init__(self):
        self.rows: list[dict] = []

    def add(self, step: str, ok: bool, code: str | None = None, **extra):
        self.rows.append({"step": step, "ok": ok, "code": code, "at": datetime.utcnow().isoformat(timespec="seconds"), **extra})


def _obs(subject: str, cluster: str | None, market: str | None, ptype: str, provider: str, **kw) -> PriceObservation:
    now = datetime.utcnow()
    return PriceObservation(subject=subject, cluster_key=cluster, market_country=market, price_type=ptype, provider=provider,
                            observed_at=now, expires_at=now + timedelta(hours=TTL_H[ptype]), **kw)


def _from_page(subject, cluster, market, got: dict, seller_is_advertiser: bool, provider: str) -> PriceObservation | None:
    if got.get("price") is None or not got.get("currency"):
        return None
    conf = float(got.get("confidence", 0.5))
    if plausible(got["price"], got["currency"], market):
        conf = min(conf, 0.3)  # keep the evidence, never show it as a verified price
    verified = conf >= VERIFIED_MIN_CONF
    ptype = (VERIFIED_COMPETITOR if seller_is_advertiser else VERIFIED_MARKETPLACE) if verified else MARKET_REFERENCE
    host = (urlparse(got.get("url") or "").hostname or "").removeprefix("www.")
    return _obs(subject, cluster, market, ptype, provider, price_amount=got["price"], price_low=got["price"], price_high=(got.get("evidence") or {}).get("price_high") or got["price"],
                currency=got["currency"].upper(), original_price=got.get("original_price"), seller=got.get("seller") or host, extraction_method=got.get("method"),
                source_url=got.get("url"), source_country=None, match_confidence=1.0 if seller_is_advertiser else 0.6, price_confidence=conf,
                evidence={k: v for k, v in (got.get("evidence") or {}).items() if k != "price_high"} | {"title": got.get("title")})


def fresh(db: Session, subject: str | None = None, cluster: str | None = None, types: tuple[str, ...] | None = None) -> list[PriceObservation]:
    q = select(PriceObservation).where(PriceObservation.expires_at > datetime.utcnow())
    if subject:
        q = q.where(PriceObservation.subject == subject)
    if cluster:
        q = q.where(PriceObservation.cluster_key == cluster)
    if types:
        q = q.where(PriceObservation.price_type.in_(types))
    return db.scalars(q.order_by(PriceObservation.observed_at.desc()).limit(20)).all()


PLACEHOLDER = re.compile(r"chưa đặt tên|unnamed|untitled|\(no name\)|^\s*$", re.I)
STOP = {"the", "and", "for", "with", "you", "your", "our", "new", "now", "free", "sale", "shop", "buy", "only", "best", "mas", "ang", "nga", "para", "din"}


def tokens(s: str | None) -> set[str]:
    return {t for t in re.findall(r"[^\W\d_]{3,}", (s or "").lower()) if t not in STOP}


def overlap(query: str, title: str | None) -> int:
    """Significant words a result title shares with the query — the cheap same-product gate for text providers."""
    return len(tokens(query) & tokens(title))


def query_text(*parts: str | None) -> str | None:
    """Product name for a text search: first non-empty, non-placeholder part, emoji / hashtags / prices stripped, ≤ 80 chars."""
    for p in parts:
        if not p or PLACEHOLDER.search(p):
            continue
        s = re.sub(r"[#@]\w+|https?://\S+|[^\w\s'&-]", " ", p)
        s = re.sub(r"\b\d[\d.,]*\s*(php|usd|eur|sar|aed|\$|₱|€)?\b", " ", s, flags=re.I)
        s = re.sub(r"\s+", " ", s).strip()
        if len(s) >= 4 and len(s.split()) <= 8:  # a sentence is ad copy, not a product name — no provider can price it
            return s[:80]
    return None


# ------------------------------------------------------------------ rungs
def rung_page(subject, cluster, market, url: str, seller_is_advertiser: bool, steps: Step, allow_brightdata: bool = True) -> PriceObservation | None:
    """Direct read; a page that blocks plain HTTP (401/403) may go to Bright Data scrape (one request)."""
    t = time.time()
    try:
        got = extract.extract(url)
        steps.add("direct", True, got.get("method"), ms=int((time.time() - t) * 1000), url=url)
        return _from_page(subject, cluster, market, got, seller_is_advertiser, "direct")
    except extract.ExtractError as e:
        steps.add("direct", False, e.code, ms=int((time.time() - t) * 1000), url=url)
        if e.code != "blocked" or not allow_brightdata or brightdata.unavailable("scrape"):
            return None
    except Exception as e:  # noqa: BLE001 — one odd page (parser surprise) must not fail the whole subject
        log.warning("direct read %s: %s: %s", url, type(e).__name__, str(e)[:120])
        steps.add("direct", False, "parser_error", ms=int((time.time() - t) * 1000), url=url, detail=f"{type(e).__name__}: {str(e)[:80]}")
        return None
    t = time.time()
    try:
        md = brightdata.scrape_markdown(url)
    except (brightdata.BrightDataError, QuotaError) as e:
        steps.add("brightdata_scrape", False, e.code, ms=int((time.time() - t) * 1000), url=url)
        return None
    got = extract.markdown_price(md, market)
    steps.add("brightdata_scrape", bool(got), "markdown" if got else "no_price", ms=int((time.time() - t) * 1000), url=url)
    if not got:
        return None
    got["url"] = url
    return _from_page(subject, cluster, market, got, seller_is_advertiser, "brightdata")


def rung_reference(subject, cluster, market, query: str, steps: Step) -> list[PriceObservation]:
    """Cluster reference: Bright Data search (prices in snippets + direct read of the first shop link); SerpApi
    Shopping only when Bright Data cannot answer. Returns [] when neither provider could be used."""
    out: list[PriceObservation] = []
    country = market if market and market != "ALL" else None
    t = time.time()
    try:
        rows = brightdata.search(f"{query} price", country)
        steps.add("brightdata_search", True, None, ms=int((time.time() - t) * 1000), results=len(rows), q=query)
    except (brightdata.BrightDataError, QuotaError) as e:
        steps.add("brightdata_search", False, e.code, ms=int((time.time() - t) * 1000), q=query)
        rows = None
    if rows is not None:
        want = MARKET_CURRENCY.get(country or "")
        prices = [(a, c) for r in rows if overlap(query, r["title"]) >= 2 for a, c in r["prices"] if not want or c == want]
        rng = coherent_range([a for a, _ in prices]) if prices else None
        if prices and not rng:
            steps.add("anomaly", False, "incoherent_range", n=len(prices))
        if rng:
            cur = statistics.mode(c for _, c in prices)
            out.append(_obs(subject, cluster, market, MARKET_REFERENCE, "brightdata", price_amount=rng[0], price_low=rng[1], price_high=rng[2],
                            currency=cur, extraction_method="serp_snippet", match_confidence=0.5, price_confidence=0.5, seller=None,
                            evidence={"query": query, "n": len(prices), "links": [r["link"] for r in rows if r["prices"]][:5]}))
        for r in rows[:6]:  # first readable shop page → a verified marketplace / shop price (free direct reads, ≤ 2)
            if NOT_A_SHOP.search(r["link"]) or SKIP_HOST.search(urlparse(r["link"]).netloc) or overlap(query, r["title"]) < 2:
                continue
            o = rung_page(subject, cluster, market, r["link"], False, steps, allow_brightdata=False)
            if o:
                out.append(o)
                break
            if sum(1 for s in steps.rows if s["step"] == "direct") >= 3:
                break
        return out  # Bright Data answered: Shopping only steps in when Bright Data could not (error / quota / disabled)
    if country and not shopping.unavailable():
        t = time.time()
        try:
            offers = shopping.search(query, country)
            steps.add("serpapi_shopping", True, None, ms=int((time.time() - t) * 1000), results=len(offers), q=query)
        except QuotaError as e:
            steps.add("serpapi_shopping", False, e.code, ms=int((time.time() - t) * 1000), q=query)
            offers = []
        priced = [o for o in offers if o["price"] and o["currency"] and overlap(query, o["title"]) >= 2]
        if offers and not priced:
            steps.add("match", False, "no_title_overlap", offers=len(offers))
        rng = coherent_range([o["price"] for o in priced]) if priced else None
        if priced and not rng:
            steps.add("anomaly", False, "incoherent_range", n=len(priced))
        if rng:
            out.append(_obs(subject, cluster, market, MARKET_REFERENCE, "serpapi_shopping", price_amount=rng[0], price_low=rng[1], price_high=rng[2],
                            currency=priced[0]["currency"], extraction_method="api", match_confidence=0.5, price_confidence=0.7, seller=None,
                            evidence={"query": query, "n": len(priced), "offers": [{"seller": o["seller"], "price": o["price"], "title": o["title"]} for o in priced[:5]]}))
    return out


# ------------------------------------------------------------------ subjects
def resolve(db: Session, subject: str, steps: Step) -> list[PriceObservation]:
    kind, _, sid = subject.partition(":")
    fn = {"ad": resolve_ad, "product": resolve_product, "listing": resolve_listing}.get(kind)
    if not fn or not sid.isdigit():
        raise ValueError(f"unknown subject {subject}")
    return fn(db, int(sid), subject, steps)


def resolve_ad(db: Session, ad_id: int, subject: str, steps: Step) -> list[PriceObservation]:
    a = db.get(Ad, ad_id)
    if not a:
        raise ValueError("ad not found")
    market = known_country(a)
    cluster = f"product:{a.product_id}" if a.product_id else None
    adv_host = (urlparse(a.landing_url or "").hostname or "").removeprefix("www.")
    out: list[PriceObservation] = []
    # 1. what the ad already carries (copy regex / landing read by services.spy / connector) — only with a currency
    bad = plausible(a.price, a.currency, market) if a.price else "no_price"
    if a.price and bad and bad != "no_currency":
        steps.add("ad_row", False, bad, price=a.price, currency=a.currency)
    if a.price and a.currency and not bad:
        conf = {"landing": 0.8, "source": 0.9}.get(a.price_source or "", 0.7)
        out.append(_obs(subject, cluster, market, VERIFIED_COMPETITOR, a.price_source or "source", price_amount=a.price, price_low=a.price, price_high=a.price,
                        currency=a.currency.upper(), original_price=a.original_price, seller=adv_host or None, extraction_method=a.price_source,
                        source_url=a.landing_url, match_confidence=1.0, price_confidence=conf, evidence={"from": "ads row"}))
        steps.add("ad_row", True, a.price_source)
        if conf >= 0.8:
            return out
    # 2. the advertiser's own page (not an inbox funnel / app store / bot-walled marketplace)
    if a.landing_url and not SKIP_HOST.search(urlparse(a.landing_url).netloc) and (a.funnel or "") not in ("mess", "form", "app"):
        o = rung_page(subject, cluster, market, a.landing_url, True, steps)
        if o:
            out.append(o)
            if o.price_type == VERIFIED_COMPETITOR:
                return out
    else:
        steps.add("landing", False, "no_public_page", funnel=a.funnel)
    # 3. cluster reference already in memory (another ad of the same product looked it up)
    if cluster:
        have = [r for r in fresh(db, cluster=cluster, types=(MARKET_REFERENCE, VERIFIED_MARKETPLACE)) if r.market_country == market]
        if have:
            steps.add("cluster_memory", True, None, n=len(have))
            for r in have[:2]:
                out.append(_obs(subject, cluster, market, r.price_type, r.provider, price_amount=r.price_amount, price_low=r.price_low, price_high=r.price_high,
                                currency=r.currency, seller=r.seller, extraction_method=r.extraction_method, source_url=r.source_url,
                                match_confidence=r.match_confidence, price_confidence=r.price_confidence, evidence={"copied_from": r.id}))
            return out
    # 4./5. search the market
    p = db.get(Product, a.product_id) if a.product_id else None
    q = query_text(p.canonical_name if p else None, a.title, a.raw_product_name)  # never the ad copy
    if q:
        out.extend(rung_reference(subject, cluster, market, q, steps))
    else:
        steps.add("query", False, "no_product_name")
    return out


def resolve_product(db: Session, pid: int, subject: str, steps: Step) -> list[PriceObservation]:
    p = db.get(Product, pid)
    if not p:
        raise ValueError("product not found")
    markets = (p.features or {}).get("markets") or ([p.country] if p.country else [])
    market = next((m for m in markets if m and m != "ALL"), None)
    cluster = subject
    # an ad of this product with a verified price → the product shows it (cheapest: no network)
    ad = next((x for x in db.scalars(select(Ad).where(Ad.product_id == pid, Ad.price.is_not(None), Ad.currency.is_not(None)).order_by(Ad.last_seen_at.desc()).limit(5))
               if not plausible(x.price, x.currency, market)), None)
    if ad:
        steps.add("ad_row", True, ad.price_source, ad_id=ad.id)
        return [_obs(subject, cluster, market, VERIFIED_COMPETITOR, ad.price_source or "source", price_amount=ad.price, price_low=ad.price, price_high=ad.price,
                     currency=ad.currency.upper(), original_price=ad.original_price, seller=(urlparse(ad.landing_url or "").hostname or "").removeprefix("www.") or None,
                     extraction_method=ad.price_source, source_url=ad.landing_url, match_confidence=1.0, price_confidence=0.8, evidence={"ad_id": ad.id})]
    # a landing page of one of its ads
    for a in db.scalars(select(Ad).where(Ad.product_id == pid, Ad.landing_url.is_not(None)).order_by(Ad.last_seen_at.desc()).limit(5)):
        if SKIP_HOST.search(urlparse(a.landing_url).netloc) or (a.funnel or "") in ("mess", "form", "app"):
            continue
        o = rung_page(subject, cluster, market, a.landing_url, True, steps)
        if o:
            return [o]
        break  # one page read per run; the next run (after TTL) tries again
    q = query_text(p.canonical_name)
    if not q:
        steps.add("query", False, "no_product_name")
        return []
    return rung_reference(subject, cluster, market, q, steps)


def resolve_listing(db: Session, lid: int, subject: str, steps: Step) -> list[PriceObservation]:
    l = db.get(EUListing, lid)
    if not l:
        raise ValueError("listing not found")
    cluster = f"eu_product:{l.product_id}" if l.product_id else None
    if not l.url:
        steps.add("listing", False, "no_url")
        return []
    o = rung_page(subject, cluster, l.market_country, l.url, False, steps)
    return [o] if o else []


def summary(db: Session, subjects: list[str]) -> dict[str, dict]:
    """Best fresh observation per subject (verified beats reference beats not-found), as the card shows it."""
    if not subjects:
        return {}
    rows = db.scalars(select(PriceObservation).where(PriceObservation.subject.in_(subjects), PriceObservation.expires_at > datetime.utcnow())
                      .order_by(PriceObservation.observed_at.desc())).all()
    rank = {VERIFIED_COMPETITOR: 0, VERIFIED_MARKETPLACE: 1, MARKET_REFERENCE: 2, NOT_FOUND: 3}
    best: dict[str, PriceObservation] = {}
    for r in rows:
        b = best.get(r.subject)
        if b is None or (rank[r.price_type], -r.price_confidence) < (rank[b.price_type], -b.price_confidence):
            best[r.subject] = r
    return {s: {"subject": s, "price_type": r.price_type, "price": r.price_amount, "low": r.price_low, "high": r.price_high, "currency": r.currency,
                "original_price": r.original_price, "seller": r.seller, "provider": r.provider, "method": r.extraction_method, "url": r.source_url,
                "confidence": r.price_confidence, "match_confidence": r.match_confidence, "observed_at": r.observed_at, "expires_at": r.expires_at,
                "market": r.market_country, "status": "done"} for s, r in best.items()}
