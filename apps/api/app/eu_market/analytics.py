"""Price / Trend / Rarity / Supplier + Ad matching / Watchlist alerts (spec §7.2–7.6, P2–P3).

Every score here is an internal signal over the data we collected — never sales, revenue or ROAS. Each result says
which inputs it had, which were missing, and flags `insufficient_data` instead of extrapolating.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import ADS_ONLY, Ad, Alert
from .models import EUAdMatch, EUListing, EUPriceSnapshot, EUProduct, EUProductMatch, EUTrendSignal, EUWatchlist
from .service import fold, to_eur, tokens

# products listed for these are never suggested (Rare Finds / Trending) — a marketer can still un-exclude by hand
COMPLIANCE_TERMS = ("replica", "counterfeit", "fake ", "knockoff", "1:1 copy", "taser", "stun gun", "pepper spray",
                    "brass knuckle", "switchblade", "butterfly knife", "nunchaku", "airsoft", "laser pointer class",
                    "nicotine", "vape", "e-liquid", "cbd", "kratom", "poppers", "steroid", "sarms", "weight loss pill")
MANUAL_TAGS = ("unusual_design", "novelty", "niche", "limited_supply", "emerging")


def compliance_flags(title: str | None) -> list[str]:
    t = f" {fold(title)} "
    return [w.strip() for w in COMPLIANCE_TERMS if w in t]


# ------------------------------------------------------------------ price
def price_series(db: Session, listing_ids: list[int], since: datetime | None, fx: dict) -> dict[int, list[dict]]:
    q = select(EUPriceSnapshot).where(EUPriceSnapshot.listing_id.in_(listing_ids or [-1])).order_by(EUPriceSnapshot.captured_at)
    if since:
        q = q.where(EUPriceSnapshot.captured_at >= since)
    out: dict[int, list[dict]] = defaultdict(list)
    for s in db.scalars(q).all():
        out[s.listing_id].append({"price": s.price, "shipping_price": s.shipping_price, "currency": s.currency,
                                  "captured_at": s.captured_at, "price_eur": to_eur(s.price, s.currency, fx)})
    return out


def price_stats(points: list[dict]) -> dict:
    """Same-currency stats over real snapshots. One point = a price, not a trend."""
    if not points:
        return {"snapshots": 0, "trend": None}
    cur = {p["currency"] for p in points}
    prices = [p["price"] for p in points]
    st = {"snapshots": len(points), "currency": next(iter(cur)) if len(cur) == 1 else None, "first": prices[0], "last": prices[-1],
          "min": min(prices), "max": max(prices), "trend": None, "change_pct": None, "consecutive_rises": 0}
    if len(points) < 2 or len(cur) != 1:
        st["note"] = "need ≥2 snapshots in one currency for a price trend"
        return st
    st["change_pct"] = round((prices[-1] - prices[0]) / prices[0] * 100, 1) if prices[0] else None
    st["trend"] = "up" if prices[-1] > prices[0] else "down" if prices[-1] < prices[0] else "flat"
    rises = 0
    for a, b in zip(reversed(prices[:-1]), reversed(prices[1:])):
        if b > a:
            rises += 1
        else:
            break
    st["consecutive_rises"] = rises
    return st


def landed_cost(l: EUListing) -> dict:
    """Only when the inputs exist: retail price incl. VAT + known shipping. Duty / VAT on B2B prices is not modelled."""
    missing = [n for n, ok in (("price", l.price is not None), ("shipping", l.shipping_price is not None),
                               ("vat_included", l.price_includes_vat is True)) if not ok]
    if missing:
        return {"value": None, "missing": missing}
    return {"value": round(l.price + l.shipping_price, 2), "currency": l.currency, "basis": "price incl. VAT + shipping"}


def compare_offers(listings: list[EUListing], fx: dict) -> dict:
    """Cross-source gap in EUR (reference rates). Retail (incl. VAT) and wholesale (excl. VAT) are reported apart."""
    groups: dict[str, list[tuple[float, EUListing]]] = {"retail_incl_vat": [], "wholesale_excl_vat": [], "vat_unknown": []}
    for l in listings:
        e = to_eur(l.price, l.currency, fx)
        if not e:
            continue
        g = "retail_incl_vat" if l.price_includes_vat is True else "wholesale_excl_vat" if l.price_includes_vat is False else "vat_unknown"
        groups[g].append((e["eur"], l))
    out = {}
    for g, rows in groups.items():
        if not rows:
            continue
        lo, hi = min(rows, key=lambda r: r[0]), max(rows, key=lambda r: r[0])
        out[g] = {"offers": len(rows), "min_eur": lo[0], "min_listing": lo[1].id, "max_eur": hi[0], "max_listing": hi[1].id,
                  "gap_pct": round((hi[0] - lo[0]) / lo[0] * 100, 1) if lo[0] and len(rows) > 1 else None}
    if out.get("retail_incl_vat") and out.get("wholesale_excl_vat"):
        r, w = out["retail_incl_vat"]["min_eur"], out["wholesale_excl_vat"]["min_eur"]
        out["retail_vs_wholesale"] = {"retail_min_eur": r, "wholesale_min_eur": w, "gross_gap_eur": round(r - w, 2),
                                      "note": "retail includes VAT, wholesale excludes VAT — not a margin"}
    return out


# ------------------------------------------------------------------ trend
def _first_last(rows: list[EUTrendSignal]):
    rows = sorted(rows, key=lambda r: r.captured_at)
    return (rows[0], rows[-1]) if len(rows) >= 2 else (None, None)


def trend_score(listings: list[EUListing], signals: list[EUTrendSignal], ad_confirmed: int, price_points: dict, now: datetime,
                window_days: int = 30) -> dict:
    """0-100 internal signal from what we observed; components without data are listed as missing, not zero-filled."""
    since = now - timedelta(days=window_days)
    first_seen = min((l.observed_at for l in listings), default=now)
    history_days = (now - first_seen).days
    comps, missing = {}, []
    new = [l for l in listings if l.observed_at >= now - timedelta(days=7)]
    comps["new_listings_7d"] = {"value": len(new), "score": min(1.0, len(new) / 5)}
    comps["source_presence"] = {"value": len({l.source for l in listings}), "score": min(1.0, (len({l.source for l in listings}) - 1) / 2)}
    comps["country_presence"] = {"value": len({l.market_country for l in listings}),
                                 "score": min(1.0, (len({l.market_country for l in listings}) - 1) / 3)}
    win = [s for s in signals if s.captured_at >= since]
    by = defaultdict(list)
    for s in win:
        by[(s.listing_id, s.signal_type)].append(s)
    rank_moves = [(a.value - b.value) for (_, t), rows in by.items() if t == "search_rank" for a, b in [_first_last(rows)] if a]
    if rank_moves:  # positive = moved up in the source's search results
        comps["search_rank_change"] = {"value": round(sum(rank_moves) / len(rank_moves), 1),
                                       "score": max(0.0, min(1.0, 0.5 + sum(rank_moves) / len(rank_moves) / 20))}
    else:
        missing.append("search_rank_change")
    rating_moves = [(b.value - a.value) for (_, t), rows in by.items() if t == "rating_count" for a, b in [_first_last(rows)] if a]
    if rating_moves:
        comps["rating_count_growth"] = {"value": sum(rating_moves), "score": min(1.0, max(0.0, sum(rating_moves)) / 50)}
    else:
        missing.append("rating_count_growth")
    changes = [st["change_pct"] for pts in price_points.values() for st in [price_stats(pts)] if st.get("change_pct") is not None]
    if changes:
        avg = sum(changes) / len(changes)
        comps["price_change_pct"] = {"value": round(avg, 1), "score": min(1.0, abs(avg) / 20)}
    else:
        missing.append("price_change_pct")
    comps["confirmed_ad_matches"] = {"value": ad_confirmed, "score": min(1.0, ad_confirmed / 3)}
    # source-specific public counters (different natures — each keeps its source; none of them is a sales number)
    def growth(t: str):
        mv = [(b.value - a.value) for (_, tt), rows in by.items() if tt == t for a, b in [_first_last(rows)] if a]
        return sum(mv) if mv else None
    for t, label, div in (("favorites", "etsy_favorites_growth", 50), ("views", "etsy_views_growth", 2000), ("offer_count", "offer_count_change", 5)):
        g = growth(t)
        if g is not None:
            comps[label] = {"value": g, "score": max(0.0, min(1.0, g / div))}
    src_of = {"new_listings_7d": "collected listings", "source_presence": "collected listings", "country_presence": "collected listings",
              "search_rank_change": "marketplace search position (Kaufland / bol / Etsy)", "rating_count_growth": "marketplace rating counts",
              "price_change_pct": "price snapshots", "confirmed_ad_matches": "SpyAds ads reviewed by a marketer",
              "etsy_favorites_growth": "Etsy num_favorers", "etsy_views_growth": "Etsy views", "offer_count_change": "Kaufland / idealo offer counts"}
    for k, c in comps.items():
        c["source"] = src_of.get(k, "collected data")
    score = round(100 * sum(c["score"] for c in comps.values()) / len(comps), 1)
    insufficient = history_days < 7 or len(missing) >= 3
    observed = len(win) + len(listings)
    confidence = "low" if insufficient or observed < 5 else "medium" if history_days < 30 or len(missing) >= 2 else "high"
    return {"score": score, "window_days": window_days, "history_days": history_days, "components": comps, "missing": missing,
            "insufficient_data": insufficient, "confidence": confidence,
            "sources": sorted({l.source for l in listings}),
            "observation_window": {"from": (now - timedelta(days=window_days)).date().isoformat(), "to": now.date().isoformat(),
                                   "first_observed": first_seen.date().isoformat()},
            "windows_available": [w for w in (7, 30, 90) if history_days >= w],
            "label": "Estimated Trend Signal",
            "note": "internal signal from collected listings/snapshots — not sales; rank / reviews / favorites / badges are never converted to orders"}


# ------------------------------------------------------------------ rarity
def rarity(product: EUProduct, listings: list[EUListing], similar: int, dataset_listings: int, now: datetime,
           price_eur: float | None, band: tuple[float, float] | None) -> dict:
    """rare_in_dataset only: few equivalent listings / similar products in OUR collected data, new, narrow coverage."""
    why, s = [], 0.0
    if len(listings) <= 2:
        s += 0.3
        why.append(f"{len(listings)} listing(s) of this product in the collected data")
    if similar <= 2:
        s += 0.25
        why.append(f"{similar} similar product(s) by title in the collected data")
    first = min((l.observed_at for l in listings), default=now)
    if now - first <= timedelta(days=14):
        s += 0.15
        why.append("first observed within 14 days")
    countries = {l.market_country for l in listings}
    if len(countries) == 1:
        s += 0.1
        why.append(f"only seen in {next(iter(countries))}")
    if price_eur is not None and band and (price_eur < band[0] or price_eur > band[1]):
        s += 0.1
        why.append("price outside the usual band of similar products")
    tags = [t for t in product.marketer_tags or [] if t in MANUAL_TAGS]
    if tags:
        s += 0.1
        why.append("marketer flags: " + ", ".join(tags))
    confidence = "low" if dataset_listings < 200 else "medium" if dataset_listings < 2000 else "high"
    return {"score": round(min(1.0, s) * 100), "label": "rare_in_dataset", "reasons": why, "dataset_listings": dataset_listings,
            "confidence": confidence, "note": "rare within the sources we collected — not a claim about the whole EU market"}


def jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


# ------------------------------------------------------------------ supplier matching (retail ↔ supplier offer)
def match_suppliers(db: Session) -> dict:
    """EAN equality = confirmed (0.95). Same brand + title Jaccard ≥ 0.6 = pending review (score = Jaccard)."""
    offers = db.scalars(select(EUListing).where(EUListing.kind == "supplier_offer")).all()
    retail = db.scalars(select(EUListing).where(EUListing.kind == "marketplace_listing")).all()
    have = {(m.product_id, m.supplier_listing_id) for m in db.scalars(select(EUProductMatch)).all()}
    by_ean = defaultdict(set)
    for r in retail:
        if r.ean and r.product_id:
            by_ean[r.ean].add(r.product_id)
    toks = {r.product_id: (fold(r.brand), tokens(r.title)) for r in retail if r.product_id}
    added = 0
    for o in offers:
        cand: dict[int, tuple[str, float, dict]] = {}
        for pid in by_ean.get(o.ean, ()) if o.ean else ():
            cand[pid] = ("ean", 0.95, {"ean": o.ean})
        ot = tokens(o.title)
        for pid, (brand, tt) in toks.items():  # ponytail: O(offers × products) — fine to ~10k each; index by token past that
            if pid in cand or pid == o.product_id or not brand or brand != fold(o.brand):
                continue
            j = jaccard(ot, tt)
            if j >= 0.6:
                cand[pid] = ("title", round(j, 2), {"brand": o.brand, "jaccard": round(j, 2)})
        for pid, (method, score, ev) in cand.items():
            if pid != o.product_id and (pid, o.id) not in have:
                db.add(EUProductMatch(product_id=pid, supplier_listing_id=o.id, match_method=method, score=score, evidence=ev,
                                      review_status="confirmed" if method == "ean" else "pending"))
                have.add((pid, o.id))
                added += 1
    db.commit()
    return {"added": added}


# ------------------------------------------------------------------ ad matching (existing SpyAds index)
def _norm_url(u: str | None) -> str | None:
    if not u:
        return None
    from ..services.spy import norm_url

    return norm_url(u)


def match_ads(db: Session, product_ids: list[int] | None = None, title_limit: int = 20) -> dict:
    """URL (strong, 0.9) / EAN in ad text (strong, 0.9) / all distinctive title words in ad text (weak, 0.4 → review).
    Only ads already collected (ADS_ONLY rows); nothing is downloaded."""
    q = select(EUProduct).where(EUProduct.excluded.is_(False))
    if product_ids:
        q = q.where(EUProduct.id.in_(product_ids))
    have = {(m.product_id, m.ad_id) for m in db.scalars(select(EUAdMatch)).all()}
    added = 0
    for p in db.scalars(q).all():
        ls = db.scalars(select(EUListing).where(EUListing.product_id == p.id)).all()
        found: dict[int, tuple[str, float, dict]] = {}
        for l in ls:
            u = _norm_url(l.url)
            if u:
                for a in db.scalars(select(Ad).where(ADS_ONLY, Ad.landing_url.ilike(f"%{u.split('://', 1)[-1]}%")).limit(50)).all():
                    found.setdefault(a.id, ("url", 0.9, {"listing_url": l.url, "ad_landing_url": a.landing_url}))
        if p.ean:
            for a in db.scalars(select(Ad).where(ADS_ONLY, func.coalesce(Ad.search_text, "").contains(p.ean)).limit(50)).all():
                found.setdefault(a.id, ("ean", 0.9, {"ean": p.ean}))
        words = sorted(tokens(p.canonical_title), key=len, reverse=True)[:4]
        if len(words) >= 3:
            cond = [func.coalesce(Ad.search_text, "").contains(w) for w in words]
            for a in db.scalars(select(Ad).where(ADS_ONLY, *cond).limit(title_limit)).all():
                found.setdefault(a.id, ("title", 0.4, {"words": words}))
        for ad_id, (method, score, ev) in found.items():
            if (p.id, ad_id) not in have:
                db.add(EUAdMatch(product_id=p.id, ad_id=ad_id, match_method=method, score=score, evidence=ev,
                                 review_status="confirmed" if method in ("url", "ean") else "pending"))
                have.add((p.id, ad_id))
                added += 1
    db.commit()
    return {"added": added}


# ------------------------------------------------------------------ watchlist alerts (reuse the app's Alert table)
def evaluate_watchlists(db: Session, fx: dict, now: datetime | None = None) -> dict:
    now = now or datetime.utcnow()
    made = 0
    for w in db.scalars(select(EUWatchlist).where(EUWatchlist.active.is_(True))).all():
        rules = w.rules or {}
        pids = list(w.product_ids or [])
        if w.keywords:
            from .service import keyword_filter

            q = select(EUListing.product_id).where(keyword_filter(w.keywords)).distinct()
            if w.countries:
                q = q.where(EUListing.market_country.in_(w.countries))
            pids += [p for p in db.scalars(q).all() if p]
        for pid in set(pids):
            ls = db.scalars(select(EUListing).where(EUListing.product_id == pid)).all()
            series = price_series(db, [l.id for l in ls], now - timedelta(days=90), fx)
            for l in ls:
                pts = series.get(l.id) or []
                if len(pts) >= 2 and rules.get("price_drop_pct"):
                    a, b = pts[-2]["price"], pts[-1]["price"]
                    if a and (a - b) / a * 100 >= float(rules["price_drop_pct"]):
                        made += _alert(db, "EU_PRICE_DROP", f"Giá giảm {round((a - b) / a * 100, 1)}%: {l.title[:80]}",
                                       f"{l.source} {l.market_country}: {a} → {b} {l.currency}", w, l, pts[-1]["captured_at"])
                st = price_stats(pts)
                if rules.get("consecutive_rises") and st.get("consecutive_rises", 0) >= int(rules["consecutive_rises"]):
                    made += _alert(db, "EU_PRICE_RISING", f"Giá tăng {st['consecutive_rises']} lần liên tiếp: {l.title[:80]}",
                                   f"{l.source} {l.market_country}: {st['first']} → {st['last']} {l.currency}", w, l, pts[-1]["captured_at"])
            if rules.get("source_gap_pct"):
                gap = (compare_offers(ls, fx).get("retail_incl_vat") or {}).get("gap_pct")
                if gap is not None and gap >= float(rules["source_gap_pct"]):
                    made += _alert(db, "EU_SOURCE_GAP", f"Chênh giá giữa nguồn {gap}%", f"product #{pid}: {len(ls)} offers", w, None,
                                   now.date())
    db.commit()
    return {"alerts": made}


def _alert(db: Session, typ: str, title: str, msg: str, w: EUWatchlist, l: EUListing | None, stamp) -> int:
    key = f"{typ}:{w.id}:{l.id if l else ''}:{stamp}"[:128]
    if db.scalar(select(Alert.id).where(Alert.dedupe_key == key)):
        return 0
    db.add(Alert(type=typ, severity="warn", title=title[:255], message=msg, dedupe_key=key,
                 data={"eu_watchlist_id": w.id, "eu_listing_id": l.id if l else None, "eu_product_id": l.product_id if l else None}))
    return 1

