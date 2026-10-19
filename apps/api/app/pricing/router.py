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
from ..models import Ad, Advertiser, Creative, Product
from ..services.spy import SKIP_HOST
from . import brightdata, extract, shopping
from .models import MARKET_REFERENCE, NOT_FOUND, VERIFIED_COMPETITOR, VERIFIED_MARKETPLACE, PriceObservation
from .quota import QuotaError, env_int
from . import quota
from .identity import matched, search_name, local_sources

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


ARTICLE_PATH = re.compile(r"/(blogs?|news|articles?|posts?|reviews?|advertorial|stories|story)/", re.I)
LISTICLE = re.compile(r"\b(top|best)\s+\d+\b|\b\d+\s+best\b|\bbest\b.*\b(of|in|for)\s+20\d\d\b|^(top|best)\b.+\b(of|in)\s*(20\d\d)?\s*$|\b(reviews?|vs\.?|comparison|ranked)\b", re.I)


def is_advertorial(url: str | None, *names: str | None) -> bool:
    """A blog / review / "Top 5 …" page sells no single product: no provider can price it, so never spend a lookup on it."""
    return bool(url and ARTICLE_PATH.search(urlparse(url).path + "/")) or any(n and LISTICLE.search(n) for n in names)


class Step:
    """Collects {step, ok, code, ms} rows — the job's audit trail (why a price is / isn't there)."""

    def __init__(self):
        self.rows: list[dict] = []

    def add(self, step: str, ok: bool, code: str | None = None, **extra):
        self.rows.append({"step": step, "ok": ok, "code": code, "at": datetime.utcnow().isoformat(timespec="seconds"), **extra})


def guard(steps: Step, name: str, fn, default=None):
    """One rung that blows up (a query / parser bug) must not take the subject, or the "Hiện giá" request, down with it: it is
    logged as an internal_error step (the card says so, the job retries) and the ladder goes on to the next rung."""
    try:
        return fn()
    except (QuotaError, ValueError):
        raise
    except Exception as e:  # noqa: BLE001
        log.exception("price rung %s failed", name)
        steps.add(name, False, "internal_error", detail=f"{type(e).__name__}: {str(e)[:120]}")
        return default


def _obs(subject: str, cluster: str | None, market: str | None, ptype: str, provider: str, **kw) -> PriceObservation:
    now = datetime.utcnow()
    return PriceObservation(subject=subject, cluster_key=cluster, market_country=market, price_type=ptype, provider=provider,
                            observed_at=now, expires_at=now + timedelta(hours=TTL_H[ptype]), **kw)


def _from_page(subject, cluster, market, got: dict, seller_is_advertiser: bool, provider: str) -> PriceObservation | None:
    if got.get("price") is None or not got.get("currency"):
        return None
    conf = float(got.get("confidence", 0.5))
    if plausible(got["price"], got["currency"], market):
        return None  # ₹569 on a Gulf product, $0.01, $90,000: a page that is not this product's market price — not stored, never shown
    verified = conf >= VERIFIED_MIN_CONF
    ptype = (VERIFIED_COMPETITOR if seller_is_advertiser else VERIFIED_MARKETPLACE) if verified else MARKET_REFERENCE
    host = (urlparse(got.get("url") or "").hostname or "").removeprefix("www.")
    return _obs(subject, cluster, market, ptype, provider, price_amount=got["price"], price_low=got["price"], price_high=(got.get("evidence") or {}).get("price_high") or got["price"],
                currency=got["currency"].upper(), original_price=got.get("original_price"), seller=got.get("seller") or host, extraction_method=got.get("method"),
                source_url=got.get("url"), source_country=None, match_confidence=1.0 if seller_is_advertiser else 0.6, price_confidence=conf,
                evidence={k: v for k, v in (got.get("evidence") or {}).items() if k != "price_high"}
                | {"title": got.get("title"), "product_identity": {k: v for k, v in {
                   "title": got.get("title"), "brand": got.get("brand"), "sku": got.get("sku"), "gtin": got.get("ean")}.items() if v}})


def fresh(db: Session, subject: str | None = None, cluster: str | None = None, types: tuple[str, ...] | None = None) -> list[PriceObservation]:
    q = select(PriceObservation).where(PriceObservation.expires_at > datetime.utcnow())
    if subject:
        q = q.where(PriceObservation.subject == subject)
    if cluster:
        q = q.where(PriceObservation.cluster_key == cluster)
    if types:
        q = q.where(PriceObservation.price_type.in_(types))
    return db.scalars(q.order_by(PriceObservation.observed_at.desc()).limit(20)).all()


PLACEHOLDER = re.compile(r"chưa đặt tên|chưa xác định sản phẩm|unnamed|untitled|\(no name\)|^\s*$", re.I)
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
        s = re.sub(r"[₱$€£]\s*\d[\d.,]*|\b\d[\d.,]*\s*(php|usd|eur|sar|aed|vnd)\b", " ", p, flags=re.I)
        s = re.sub(r"[#@]\w+|https?://\S+|[^\w\s'&-]", " ", s)
        s = re.sub(r"\s+", " ", s).strip()
        if len(s) >= 4 and len(s.split()) <= 8:  # a sentence is ad copy, not a product name — no provider can price it
            return s[:80]
    return None


# ------------------------------------------------------------------ rungs
def rung_page(subject, cluster, market, url: str, seller_is_advertiser: bool, steps: Step, allow_brightdata: bool = False,
              expected_query: str | None = None, expected_identity: dict | None = None) -> PriceObservation | None:
    """Direct read; a page that blocks plain HTTP (401/403) may go to Bright Data scrape (one request)."""
    t = time.time()
    try:
        got = extract.extract(url)
        steps.add("direct", True, got.get("method"), ms=int((time.time() - t) * 1000), url=url)
        if expected_query and not seller_is_advertiser:
            if not matched(expected_query, got.get("title"), expected_identity,
                           {"gtin": got.get("ean"), "sku": got.get("sku"), "brand": got.get("brand")}):
                steps.add("identity", False, "page_product_mismatch", url=url)
                return None
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


def provider_order(country: str | None) -> list[str]:
    """Which text provider goes first: the one with more of its budget left (a provider that is off / out of budget goes last; both are
    always tried before giving up, so SerpApi covers Bright Data and the other way round)."""
    from ..db import SessionLocal

    def room(provider: str, unavailable) -> float:
        if unavailable:
            return -1.0
        try:
            with SessionLocal() as db:
                r = quota.remaining(db, provider)
            return min(r["month_remaining"] / max(1, r["monthly_budget"]), r["day_remaining"] / max(1, r["daily_budget"]))
        except Exception:  # noqa: BLE001
            return 0.0

    bd = room("brightdata", brightdata.unavailable("search"))
    sp = room("serpapi_shopping", shopping.unavailable() if country else "no_country")
    return ["serpapi_shopping", "brightdata"] if sp > bd else ["brightdata", "serpapi_shopping"]


def _via_brightdata(subject, cluster, market, query, steps, country) -> list[PriceObservation]:
    out: list[PriceObservation] = []
    t = time.time()
    try:
        rows = brightdata.search(f"{query} price", country)
        steps.add("brightdata_search", True, None, ms=int((time.time() - t) * 1000), results=len(rows), q=query)
    except (brightdata.BrightDataError, QuotaError) as e:
        steps.add("brightdata_search", False, e.code, ms=int((time.time() - t) * 1000), q=query)
        return out
    want = MARKET_CURRENCY.get(country or "")
    prices = [(a, c) for r in rows if matched(query, r["title"]) for a, c in r["prices"] if (not want or c == want) and not plausible(a, c, country)]
    if prices and not want:  # never mix currencies into one range
        cur = statistics.mode(c for _, c in prices)
        prices = [(a, c) for a, c in prices if c == cur]
    rng = coherent_range([a for a, _ in prices]) if prices else None
    if prices and not rng:
        steps.add("anomaly", False, "incoherent_range", n=len(prices))
    if rng:
        cur = statistics.mode(c for _, c in prices)
        out.append(_obs(subject, cluster, market, MARKET_REFERENCE, "brightdata", price_amount=rng[0], price_low=rng[1], price_high=rng[2],
                        currency=cur, extraction_method="serp_snippet", match_confidence=0.5, price_confidence=0.5, seller=None,
                        evidence={"query": query, "n": len(prices), "links": [r["link"] for r in rows if r["prices"]][:5]}))
    for r in rows[:6]:  # first readable shop page: a verified marketplace / shop price (free direct reads, up to 3)
        if NOT_A_SHOP.search(r["link"]) or SKIP_HOST.search(urlparse(r["link"]).netloc) or not matched(query, r["title"]):
            continue
        o = rung_page(subject, cluster, market, r["link"], False, steps, allow_brightdata=False, expected_query=query)
        if o:
            out.append(o)
            break
        if sum(1 for s in steps.rows if s["step"] == "direct") >= 3:
            break
    return out


def _via_shopping(subject, cluster, market, query, steps, country) -> list[PriceObservation]:
    out: list[PriceObservation] = []
    if not country or shopping.unavailable():
        steps.add("serpapi_shopping", False, shopping.unavailable() or "no_country")
        return out
    t = time.time()
    try:
        offers = shopping.search(query, country)
        steps.add("serpapi_shopping", True, None, ms=int((time.time() - t) * 1000), results=len(offers), q=query)
    except QuotaError as e:
        steps.add("serpapi_shopping", False, e.code, ms=int((time.time() - t) * 1000), q=query)
        return out
    priced = [o for o in offers if o["price"] and o["currency"] and matched(query, o["title"]) and not plausible(o["price"], o["currency"], country)]
    if priced:
        cur = statistics.mode(o["currency"] for o in priced)
        priced = [o for o in priced if o["currency"] == cur]
    for offer in priced[:2]:
        # Google Shopping's product_link is a google.com/search?ibp=oshop page, not the shop: reading it is a wasted fetch (measured 2026-10-14)
        if offer.get("link") and not SKIP_HOST.search(urlparse(offer["link"]).netloc) and not NOT_A_SHOP.search(urlparse(offer["link"]).netloc):
            page = rung_page(subject, cluster, market, offer["link"], False, steps, expected_query=query)
            if page:
                out.append(page)
                break
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


def rung_reference(subject, cluster, market, query: str, steps: Step) -> list[PriceObservation]:
    """Paid interactive lookup or budgeted TEST enrichment. Two providers, each the other's fallback (provider_order): Bright Data search
    (prices in snippets + direct read of the first shop link) and SerpApi Google Shopping. The first that yields a usable price stops the
    ladder; a provider that is off, out of budget, errored or came back empty hands over to the other. [] when neither could."""
    country = market if market and market != "ALL" else None
    via = {"brightdata": _via_brightdata, "serpapi_shopping": _via_shopping}
    for name in provider_order(country):
        out = guard(steps, name, lambda: via[name](subject, cluster, market, query, steps, country), [])
        if out:
            return out
    return []


# ------------------------------------------------------------------ subjects
def resolve(db: Session, subject: str, steps: Step, paid: bool = False) -> list[PriceObservation]:
    """paid=False: local/cache/direct pages. paid=True: interactive lookup or budgeted TEST enrichment."""
    kind, _, sid = subject.partition(":")
    fn = {"ad": resolve_ad, "product": resolve_product, "listing": resolve_listing}.get(kind)
    if not fn or not sid.isdigit():
        raise ValueError(f"unknown subject {subject}")
    memory = fresh(db, subject=subject, types=(VERIFIED_COMPETITOR, VERIFIED_MARKETPLACE, MARKET_REFERENCE))
    if memory:
        steps.add("memory", True, "fresh")
        return [_copy_observation(r, subject, r.cluster_key) for r in memory[:1]]
    return fn(db, int(sid), subject, steps, paid)


def _copy_observation(row, subject, cluster):
    # Reuse the source timestamp and expiry, so cache hits never make old evidence look new.
    out = _obs(subject, cluster, row.market_country, row.price_type, row.provider,
               price_amount=row.price_amount, price_low=row.price_low, price_high=row.price_high, currency=row.currency,
               source_url=row.source_url, seller=row.seller, extraction_method=row.extraction_method,
               match_confidence=row.match_confidence, price_confidence=row.price_confidence,
               evidence=dict(row.evidence or {}) | {"copied_from": row.id})
    out.observed_at, out.expires_at = row.observed_at, row.expires_at
    return out


def rung_local(db, p, subject, cluster, market, steps):
    ads = list(db.scalars(select(Ad).where(Ad.product_id == p.id)))
    out = []
    for a, method, confidence in local_sources(db, p, ads):
        if plausible(a.price, a.currency, None):
            continue
        steps.add("local_marketplace", True, method, ad_id=a.id)
        out.append(_obs(subject, cluster, market, VERIFIED_MARKETPLACE, a.source or "local", price_amount=a.price,
                        price_low=a.price, price_high=a.price, currency=a.currency, source_url=a.landing_url,
                        seller=(urlparse(a.landing_url or "").hostname or None), extraction_method=method,
                        match_confidence=confidence, price_confidence=.85,
                        evidence={"ad_id": a.id, "identity": a.product_metadata, "source_country": a.country, "role": "supplier"}))
        # Observed at source, not at the time the user opened this card.
        out[-1].observed_at = a.last_seen_at
        out[-1].expires_at = a.last_seen_at + timedelta(hours=TTL_H[VERIFIED_MARKETPLACE])
    return [o for o in out if o.expires_at > datetime.utcnow()]


def rung_lens(db, p, subject, cluster, market, steps, paid):
    """Cached image search first. Async live Lens only for TEST/TEST_NOW; queue joins the same job."""
    from ..visual import lens
    from ..visual.models import ProductImage, LensSearch
    from ..visual.service import QueryImage
    from ..storage import store
    c = db.scalar(select(Creative).where(Creative.product_id == p.id, Creative.thumb_key.is_not(None))
                  .order_by(Creative.id).limit(1))
    if not c:
        return []
    try:
        q = QueryImage(store().get(c.thumb_key))
        im = db.scalar(select(ProductImage).where(ProductImage.source == "creative", ProductImage.source_id == c.id))
        if im and im.embedding:
            from ..visual.embed import from_bytes
            q.embedding = from_bytes(im.embedding)
        key = lens.cache_key(q.sha256, q.crop, market, "en", "products", None)
        near = near_cached_search(db, q, market)  # same picture searched before (resized / re-encoded copy): its result is free
        if near is not None:
            steps.add("serpapi_lens", True, "cached_near", job_id=near.id)
            return lens_rows(near, subject, cluster, market, steps, paid, p)
        failed = db.scalar(select(LensSearch).where(LensSearch.cache_key == key, LensSearch.status == "error",
                           LensSearch.error_code.not_in(["bad_request", "internal_error"]),  # our own bugs are not a provider outage: retry at once
                           LensSearch.created_at > datetime.utcnow() - timedelta(hours=6)).order_by(LensSearch.id.desc()).limit(1))
        if failed:
            steps.add("serpapi_lens", False, failed.error_code or "provider_error", job_id=failed.id)
            return []
        job = lens.start(q, country=market, typ="products", cache_only=not (paid and p.recommendation in ("TEST", "TEST_NOW")))
    except (lens.LensError, ValueError, OSError) as e:
        steps.add("serpapi_lens", False, getattr(e, "code", "no_local_image"))
        return []
    if job["status"] == "running":
        steps.add("serpapi_lens", False, "lens_pending", job_id=job["job_id"])
        return []
    if job["status"] != "ok":
        return []
    # Lens writes in another session; refresh to read the completed job.
    search = db.get(LensSearch, job["job_id"])
    if not search:
        return []
    steps.add("serpapi_lens", True, "cached" if job.get("cached") else "ok", job_id=search.id)
    return lens_rows(search, subject, cluster, market, steps, paid, p)


def near_cached_search(db, q, market):
    """A finished Lens search of a near-identical picture (perceptual hash within EXACT_DIST: the same creative re-encoded / resized, e.g.
    another ad of the cluster) for the same market, reused before any request is bought. The hash index is the one image search built."""
    from ..visual import service as vs
    from ..visual.models import LensSearch, ProductImage

    try:
        vs.HASH_INDEX.refresh(db)
        near = vs.HASH_INDEX.near(q.phash, q.dhash, vs.EXACT_DIST)
    except Exception:  # noqa: BLE001 - optional shortcut
        return None
    if not near:
        return None
    shas = [r for (r,) in db.execute(select(ProductImage.image_sha256).where(ProductImage.id.in_(list(near)[:200]), ProductImage.image_sha256.is_not(None)))]
    if not shas:
        return None
    for s in db.scalars(select(LensSearch).where(LensSearch.image_sha256.in_(shas), LensSearch.status == "ok",
                                                  LensSearch.expires_at > datetime.utcnow()).order_by(LensSearch.id.desc()).limit(10)):
        if ((s.params or {}).get("country") or "").lower() == (market or "").lower():
            return s
    return None


def lens_rows(search, subject, cluster, market, steps, paid, p) -> list[PriceObservation]:
    out = []
    direct_reads = 0
    for row in (search.results or [])[:10]:
        if row.get("verification_status") != "visual_verified" or row.get("tier") not in ("EXACT", "LIKELY_SAME_PRODUCT"):
            continue
        if direct_reads < 2 and row.get("product_url") and not SKIP_HOST.search(urlparse(row["product_url"]).netloc):
            direct_reads += 1
            page = rung_page(subject, cluster, market, row["product_url"], False, steps,
                             allow_brightdata=paid, expected_query=search_name(p), expected_identity=p.product_identity)
            if page:
                page.evidence = dict(page.evidence or {}) | {"lens_job_id": search.id, "tier": row["tier"], "cosine": row.get("cosine")}
                out.append(page)
                break
        price, cur = row.get("price"), row.get("currency")
        if price and cur and not plausible(price, cur, market):
            out.append(_obs(subject, cluster, market, MARKET_REFERENCE, "serpapi_lens", price_amount=price,
                       price_low=price, price_high=price, currency=cur, source_url=row.get("product_url"), seller=row.get("shop_name"),
                       extraction_method="lens_result", match_confidence=row.get("similarity") or .8, price_confidence=.5,
                       evidence={"lens_job_id": search.id, "tier": row["tier"], "title": row.get("title")}))
        if len(out) >= 3:
            break
    return out


def resolve_ad(db: Session, ad_id: int, subject: str, steps: Step, paid: bool = False) -> list[PriceObservation]:
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
        out.append(_obs(subject, cluster, market, VERIFIED_MARKETPLACE if a.channel == "commerce" else VERIFIED_COMPETITOR, a.price_source or "source", price_amount=a.price, price_low=a.price, price_high=a.price,
                        currency=a.currency.upper(), original_price=a.original_price, seller=adv_host or None, extraction_method=a.price_source,
                        source_url=a.landing_url, match_confidence=1.0, price_confidence=conf,
                        evidence={"from": "ads row", "role": "supplier" if a.channel == "commerce" else "competitor"}))
        steps.add("ad_row", True, a.price_source)
        if conf >= 0.8:
            return out
    p0 = db.get(Product, a.product_id) if a.product_id else None
    if not out and is_advertorial(a.landing_url, a.title, p0.canonical_name if p0 else None):
        steps.add("advertorial", False, "advertorial", url=a.landing_url)
        return out
    # 2. the advertiser's own page (not an inbox funnel / app store / bot-walled marketplace)
    if a.landing_url and not SKIP_HOST.search(urlparse(a.landing_url).netloc) and (a.funnel or "") not in ("mess", "form", "app"):
        o = rung_page(subject, cluster, market, a.landing_url, True, steps, paid)
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
                out.append(_copy_observation(r, subject, cluster))
            return out
    # 4./5. search the market
    p = db.get(Product, a.product_id) if a.product_id else None
    if p:
        # Same cluster shares source/image lookups; an advertiser-specific price is never copied as its own price.
        out.extend(resolve_product(db, p.id, subject, steps, paid))
        return out
    q = query_text(a.raw_product_name, a.title)
    if q and not paid:
        steps.add("search", False, "needs_click")
    elif q:
        out.extend(rung_reference(subject, cluster, market, q, steps))
    else:
        steps.add("query", False, "no_product_name")
    return out


def resolve_product(db: Session, pid: int, subject: str, steps: Step, paid: bool = False) -> list[PriceObservation]:
    p = db.get(Product, pid)
    if not p:
        raise ValueError("product not found")
    markets = (p.features or {}).get("markets") or ([p.country] if p.country else [])
    market = next((m for m in markets if m and m != "ALL"), None)
    cluster = f"product:{pid}"
    have = [r for r in fresh(db, cluster=cluster, types=(MARKET_REFERENCE, VERIFIED_MARKETPLACE)) if r.market_country == market]
    if have:
        steps.add("cluster_memory", True, "fresh")
        return [_copy_observation(r, subject, cluster) for r in have[:1]]
    # an ad of this product with a verified price → the product shows it (cheapest: no network)
    ad = next((x for x in db.scalars(select(Ad).where(Ad.product_id == pid, Ad.channel.in_(["ads"]),
               Ad.is_internal.is_(False), Ad.price.is_not(None), Ad.currency.is_not(None),
               Ad.last_seen_at >= datetime.utcnow() - timedelta(days=7)).order_by(Ad.last_seen_at.desc()).limit(5))
               if not plausible(x.price, x.currency, market)), None)
    if ad:
        steps.add("ad_row", True, ad.price_source, ad_id=ad.id)
        row = _obs(subject, cluster, market, VERIFIED_COMPETITOR if subject.startswith("product:") else MARKET_REFERENCE, ad.price_source or "source", price_amount=ad.price, price_low=ad.price, price_high=ad.price,
                     currency=ad.currency.upper(), original_price=ad.original_price, seller=(urlparse(ad.landing_url or "").hostname or "").removeprefix("www.") or None,
                     extraction_method=ad.price_source, source_url=ad.landing_url, match_confidence=1.0, price_confidence=0.8, evidence={"ad_id": ad.id})
        row.observed_at = ad.last_seen_at
        row.expires_at = ad.last_seen_at + timedelta(days=7)
        return [row]
    local = guard(steps, "local_marketplace", lambda: rung_local(db, p, subject, cluster, market, steps), [])
    if local:
        return local
    lands = [a for a in db.scalars(select(Ad).where(Ad.product_id == pid, Ad.landing_url.is_not(None)).order_by(Ad.last_seen_at.desc()).limit(5))]
    if lands and all(is_advertorial(a.landing_url, a.title) for a in lands) or is_advertorial(None, p.canonical_name):
        steps.add("advertorial", False, "advertorial", url=lands[0].landing_url if lands else None)
        return []
    # a landing page of one of its ads
    for a in db.scalars(select(Ad).where(Ad.product_id == pid, Ad.landing_url.is_not(None)).order_by(Ad.last_seen_at.desc()).limit(5)):
        if SKIP_HOST.search(urlparse(a.landing_url).netloc) or (a.funnel or "") in ("mess", "form", "app"):
            continue
        o = rung_page(subject, cluster, market, a.landing_url, True, steps, paid)
        if o:
            return [o]
        break  # one page read per run; the next run (after TTL) tries again
    image_prices = guard(steps, "serpapi_lens", lambda: rung_lens(db, p, subject, cluster, market, steps, paid), [])
    if image_prices:
        return image_prices
    if any(s.get("code") == "lens_pending" for s in steps.rows):
        return []
    q = guard(steps, "name", lambda: search_name(p, list(db.scalars(select(Ad).where(Ad.product_id == pid).limit(10))), db, steps))
    if not q:
        steps.add("query", False, "no_product_name")
        return []
    if not paid:
        steps.add("search", False, "needs_click")
        return []
    return rung_reference(subject, cluster, market, q, steps)


def resolve_listing(db: Session, lid: int, subject: str, steps: Step, paid: bool = False) -> list[PriceObservation]:
    l = db.get(EUListing, lid)
    if not l:
        raise ValueError("listing not found")
    cluster = f"eu_product:{l.product_id}" if l.product_id else None
    if not l.url:
        steps.add("listing", False, "no_url")
        return []
    o = rung_page(subject, cluster, l.market_country, l.url, False, steps, paid)
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
                "market": r.market_country, "status": "done", "role": (r.evidence or {}).get("role"),
                "advertorial": r.price_type == NOT_FOUND and any(x.get("code") == "advertorial" for x in (r.evidence or {}).get("steps", []))} for s, r in best.items()}
