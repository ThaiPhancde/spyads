"""Competitor spy enrichment — what the ad libraries never give us:

1. the price a competitor actually sells at  → the ad copy (regex) or the landing page (Shopify .json / JSON-LD / og:price)
2. what customers say                        → public marketplace reviews (AliExpress feedback API) + reviews embedded in
                                               the landing page (JSON-LD `review`)
Everything is written back onto the `ads` rows (price, price_source, rating) and into `comments` (source=review /
landing_review) so Comment Intelligence, scoring and the product page pick it up without any new reader.
Runs on demand (POST /api/products/{id}/scan) and every few minutes from the scheduler (a slice of URLs per tick).
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from urllib.parse import urlparse, urlunparse

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..models import Ad, Comment, Product

log = logging.getLogger(__name__)

# no price on these pages (inbox funnel, app stores, redirect/tracking hosts, bot-walled marketplaces)
SKIP_HOST = re.compile(r"facebook\.com|fb\.com|fb\.me|m\.me|instagram\.com|wa\.me|whatsapp\.com|messenger\.com|t\.me|zalo\.me|line\.me|"
                       r"play\.google|apple\.com|onelink\.me|doubleclick|googleadservices|tiktok\.com|spotify\.com|youtube\.com|youtu\.be|"
                       r"alibaba\.com|temu\.com|amazon\.|linktr\.ee|bit\.ly|shopee\.|lazada\.", re.I)
RECHECK_DAYS = 14          # a landing price / review set is re-fetched after this long
TIMEOUT = 12
WORKERS = 6


def _num(s) -> float | None:
    """'1.016.000' / '1,016,000.00' / '74.99' / 1016000 → float (thousands vs decimals by group length)."""
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    s = re.sub(r"[^\d.,]", "", str(s))
    if not s:
        return None
    parts = re.split(r"[.,]", s)
    dec = parts.pop() if len(parts) > 1 and len(parts[-1]) != 3 else None
    try:
        return float("".join(parts) + (f".{dec}" if dec else ""))
    except ValueError:
        return None


def _walk(node, out: list):
    if isinstance(node, dict):
        out.append(node)
        for v in node.values():
            _walk(v, out)
    elif isinstance(node, list):
        for v in node:
            _walk(v, out)


def parse_landing(html: str, url: str) -> dict:
    """Pure parser (unit-testable): price / currency / compare-at / rating / reviews from a store page."""
    out: dict = {"price": None, "currency": None, "compare_at": None, "rating": None, "review_count": None, "reviews": []}
    nodes: list[dict] = []
    for raw in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', html, re.S | re.I):
        try:
            _walk(json.loads(raw.strip()), nodes)
        except ValueError:
            continue
    prices = []
    for n in nodes:
        t = str(n.get("@type", ""))
        if "Offer" in t or "price" in n or "lowPrice" in n:
            p = _num(n.get("lowPrice") or n.get("price"))
            if p:
                prices.append(p)
                out["currency"] = out["currency"] or n.get("priceCurrency")
        if "AggregateRating" in t:
            out["rating"], out["review_count"] = _num(n.get("ratingValue")), int(_num(n.get("reviewCount") or n.get("ratingCount")) or 0) or None
        if t == "Review" and (n.get("reviewBody") or n.get("description")):
            rr = n.get("reviewRating") or {}
            author = n.get("author")
            out["reviews"].append({"text": str(n.get("reviewBody") or n.get("description"))[:1000],
                                   "rating": _num(rr.get("ratingValue")) if isinstance(rr, dict) else None,
                                   "author": author.get("name") if isinstance(author, dict) else author,
                                   "date": n.get("datePublished")})
    if prices:
        out["price"] = min(prices)
    if out["price"] is None:
        m = re.search(r'(?:og|product):price:amount"\s+content="([^"]+)"', html) or re.search(r'content="([^"]+)"\s+property="(?:og|product):price:amount"', html)
        if m:
            out["price"] = _num(m.group(1))
    if out["price"] is None:
        m = re.search(r'itemprop="price"[^>]*content="([^"]+)"', html)
        if m:
            out["price"] = _num(m.group(1))
    if out["price"] is None:  # themes without structured data: the first money-looking text inside a *price* element
        from ..ingest import parse_price

        for hit in re.findall(r'class="[^"]*price[^"]*"[^>]*>\s*([^<]{1,40})', html, re.I)[:6]:
            if pc := parse_price(hit):
                out["price"], out["currency"] = pc[0], pc[1] or out["currency"]
                break
    cur = (re.search(r'Shopify\.currency\s*=\s*\{"active":"([A-Z]{3})"', html) or re.search(r'(?:og|product):price:currency"\s+content="([A-Z]{3})"', html)
           or re.search(r'itemprop="priceCurrency"[^>]*content="([A-Z]{3})"', html) or re.search(r'"currency":"([A-Z]{3})"', html))
    out["currency"] = (cur.group(1) if cur else None) or out["currency"]
    return out


def _get(url: str):
    from curl_cffi import requests as cr  # dependency of meta-ads-collector; plain httpx gets 429 from Shopify's bot wall

    return cr.get(url, impersonate="chrome", timeout=TIMEOUT, allow_redirects=True)


def fetch_landing(url: str) -> dict:
    """One store page → {price, currency, compare_at, rating, review_count, reviews, ok, status}."""
    out = {"ok": False, "status": None}
    try:
        r = _get(url)
        out["status"] = r.status_code
        if r.status_code >= 400:
            return out
        out.update(parse_landing(r.text, url))
        pu = urlparse(str(r.url))
        if out.get("price") is None and "/products/" in pu.path:  # Shopify: the product JSON always has the variants
            rj = _get(urlunparse(pu._replace(path=re.sub(r"\.json$", "", pu.path.rstrip("/")) + ".json", query="", fragment="")))
            if rj.status_code == 200 and "json" in rj.headers.get("content-type", ""):
                vs = [v for v in rj.json().get("product", {}).get("variants", []) if _num(v.get("price"))]
                if vs:
                    out["price"] = min(_num(v["price"]) for v in vs)
                    out["compare_at"] = max((_num(v.get("compare_at_price")) or 0 for v in vs), default=None) or None
        out["ok"] = out.get("price") is not None or bool(out.get("reviews"))
    except Exception as e:  # network / parse — never let one bad store stop the batch
        out["error"] = f"{type(e).__name__}: {str(e)[:120]}"
    return out


def norm_url(u: str) -> str:
    p = urlparse(u)
    return urlunparse((p.scheme or "https", p.netloc.lower().removeprefix("www."), p.path.rstrip("/"), "", "", ""))


# ------------------------------------------------------------ AliExpress public reviews (no login, no key)
def fetch_aliexpress_reviews(item_id: str, pages: int = 2) -> list[dict]:
    rows: list[dict] = []
    for page in range(1, pages + 1):
        try:
            r = _get(f"https://feedback.aliexpress.com/pc/searchEvaluation.do?productId={item_id}&lang=en_US&country=US"
                     f"&page={page}&pageSize=20&filter=all&sort=complex_default")
            data = (r.json() or {}).get("data") or {}
        except Exception as e:
            log.info("aliexpress reviews %s p%s: %s", item_id, page, e)
            break
        evs = data.get("evaViewList") or []
        for e in evs:
            text = (e.get("buyerTranslationFeedback") or e.get("buyerFeedback") or "").strip()
            if not text:
                continue
            rows.append({"external_id": f"ali:{e.get('evaluationId') or hashlib.sha1(text.encode()).hexdigest()[:16]}",
                         "text": text[:1000], "rating": (e.get("buyerEval") or 0) / 20 or None, "country": e.get("buyerCountry"),
                         "created_at": e.get("evalDate")})
        if len(evs) < 20:
            break
    return rows


def _stars_overall(r: float | None) -> str | None:
    if r is None:
        return None
    return "positive" if r >= 4 else "negative" if r <= 2 else "neutral"


def _add_comments(db: Session, rows: list[dict], product_id: int | None, ad_id: int | None, source: str) -> list[Comment]:
    ids = [r["external_id"] for r in rows]
    have = set(db.scalars(select(Comment.external_id).where(Comment.external_id.in_(ids)))) if ids else set()
    out = []
    for r in rows:
        if r["external_id"] in have:
            continue
        have.add(r["external_id"])
        created = None
        if r.get("created_at"):
            try:
                created = datetime.fromisoformat(str(r["created_at"])[:19].replace(" ", "T"))
            except ValueError:
                created = None
        c = Comment(product_id=product_id, ad_id=ad_id, source=source, text=r["text"], external_id=r["external_id"],
                    rating=r.get("rating"), country=r.get("country"), created_at=created or datetime.utcnow())
        db.add(c)
        out.append(c)
    return out


# ------------------------------------------------------------ the scan
def scan(db: Session, product_id: int | None = None, limit: int = 40, review_listings: int = 6) -> dict:
    """1) regex prices from ad copy, 2) landing-page prices (+ embedded reviews), 3) AliExpress reviews.
    Fetches run with no DB transaction open (SQLite single writer)."""
    from ..ingest import parse_price
    from .connectors import enrich_comments
    from .engine import rescore_products

    now = datetime.utcnow()
    stale = now - timedelta(days=RECHECK_DAYS)
    stats = {"text_priced": 0, "urls_fetched": 0, "landing_priced": 0, "landing_failed": 0, "reviews_added": 0, "listings_fetched": 0}
    touched: set[int] = set()
    spy = select(Ad).where(or_(Ad.channel == "ads", Ad.channel.is_(None)), Ad.is_internal.is_(False))
    if product_id:
        spy = spy.where(Ad.product_id == product_id)

    # 1. ad copy
    for a in db.scalars(spy.where(Ad.price.is_(None), Ad.price_source.is_(None), Ad.ad_text.is_not(None))).all():
        if pc := parse_price(f"{a.title or ''} {a.ad_text or ''}", a.country):
            a.price, a.currency, a.price_source = pc[0], pc[1] or a.currency, "ad_text"
            stats["text_priced"] += 1
            touched.add(a.product_id)
    db.commit()

    # 2. landing pages — one fetch per URL, result applied to every ad pointing there
    q = spy.where(Ad.landing_url.is_not(None), or_(Ad.landing_checked_at.is_(None), Ad.landing_checked_at < stale)).join(Product, Product.id == Ad.product_id)
    if product_id:
        q = q.where(Ad.price.is_(None))
    cands = db.scalars(q.order_by(Product.lifecycle_status.in_(["WATCHLIST", "TESTING", "CANDIDATE"]).desc(), Ad.first_seen_at.desc()).limit(limit * 6)).all()
    urls: dict[str, str] = {}
    for a in cands:
        if SKIP_HOST.search(urlparse(a.landing_url).netloc):
            a.landing_checked_at = now  # never fetchable: don't look again
            continue
        k = norm_url(a.landing_url)
        if k not in urls and len(urls) < limit:
            urls[k] = a.landing_url
    db.commit()
    results: dict[str, dict] = {}
    if urls:
        with ThreadPoolExecutor(WORKERS) as ex:
            for k, res in zip(urls, ex.map(fetch_landing, urls.values())):
                results[k] = res
    stats["urls_fetched"] = len(results)
    pending: list[Comment] = []  # new review rows (landing + AliExpress) → analyzed once at the end
    for k, res in results.items():
        rows = [a for a in db.scalars(select(Ad).where(Ad.landing_url.like(f"%{urlparse(k).netloc}%"), or_(Ad.channel == "ads", Ad.channel.is_(None)))).all()
                if norm_url(a.landing_url) == k]
        if not res.get("ok"):
            stats["landing_failed"] += 1
        for a in rows:
            a.landing_checked_at = now
            if res.get("price") and (a.price is None or a.price_source in (None, "ad_text", "landing")):
                a.price, a.currency, a.price_source = res["price"], res.get("currency") or a.currency, "landing"
                a.original_price = res.get("compare_at") or a.original_price
                stats["landing_priced"] += 1
                touched.add(a.product_id)
            if res.get("rating"):
                a.rating, a.review_count = res["rating"], res.get("review_count") or a.review_count
        if res.get("reviews") and rows:
            lead = rows[0]
            new = _add_comments(db, [{**r, "external_id": f"lp:{hashlib.sha1((k + r['text']).encode()).hexdigest()[:16]}", "created_at": r.get("date")}
                                     for r in res["reviews"]], lead.product_id, lead.id, "landing_review")
            pending += new
            touched.add(lead.product_id)
    db.commit()

    # 3. AliExpress reviews for the product's China-source listings
    lq = select(Ad).where(Ad.network == "aliexpress", Ad.landing_url.like("%/item/%"), or_(Ad.reviews_checked_at.is_(None), Ad.reviews_checked_at < stale))
    if product_id:
        lq = lq.where(Ad.product_id == product_id)
    else:
        lq = lq.join(Product, Product.id == Ad.product_id).where(Product.lifecycle_status.in_(["WATCHLIST", "TESTING", "CANDIDATE"]))
    listings = db.scalars(lq.order_by(Ad.sold_count.desc().nullslast()).limit(review_listings)).all()
    ids = [(l.id, l.product_id, m.group(1)) for l in listings if (m := re.search(r"/item/(\d+)", l.landing_url or ""))]
    db.commit()
    fetched = {}
    if ids:
        with ThreadPoolExecutor(WORKERS) as ex:
            for (lid, pid, item), rows in zip(ids, ex.map(fetch_aliexpress_reviews, [i[2] for i in ids])):
                fetched[lid] = (pid, rows)
    for lid, (pid, rows) in fetched.items():
        l = db.get(Ad, lid)
        l.reviews_checked_at = now
        stats["listings_fetched"] += 1
        new = _add_comments(db, rows, pid, lid, "review")
        pending += new
        if new:
            touched.add(pid)
    stats["reviews_added"] = len(pending)
    db.flush()
    if pending:  # aspects / intent from the analyzer (LLM or rules); stars stay the ground truth for `overall`
        try:
            enrich_comments(db, pending)
        except Exception:
            log.exception("comment analysis failed")
        for c in pending:
            c.overall = _stars_overall(c.rating) or c.overall or "neutral"
            c.analyzed_by = c.analyzed_by or "stars"
    db.commit()
    if touched:
        rescore_products(db, {t for t in touched if t})
        db.commit()
    stats["products_rescored"] = len(touched)
    return stats
