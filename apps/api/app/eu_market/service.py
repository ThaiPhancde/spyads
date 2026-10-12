"""Normalize → deduplicate/group → upsert, FX, and local multi-keyword search (spec §5, §7.1).

Grouping is deliberately conservative: listings join one EUProduct only on a valid EAN, or on the exact same
brand + title token set. No translation, no fuzzy merge — two different products must never collapse because they
share a keyword.
"""
from __future__ import annotations

import re
import unicodedata
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

import httpx
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from .models import EUFxRate, EUListing, EUPriceSnapshot, EUProduct, EUSupplier, EUTrendSignal

SNAPSHOT_EVERY = timedelta(hours=24)  # an unchanged price is re-recorded at most daily; any change is recorded at once
SIGNAL_EVERY = timedelta(hours=12)
ECB_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
STOP = {"the", "and", "for", "with", "de", "la", "le", "les", "der", "die", "das", "und", "mit", "en", "het", "een", "voor",
        "van", "di", "il", "per", "con", "y", "el", "para", "a", "of", "in", "z", "i", "w", "na"}


# ------------------------------------------------------------------ normalize
def fold(s: str | None) -> str:
    """lower-case, accents stripped: 'Glücksarmband' -> 'glucksarmband' (search + grouping keys)."""
    s = unicodedata.normalize("NFKD", (s or "").replace("ß", "ss")).encode("ascii", "ignore").decode()
    return s.lower()


def norm_ean(v) -> str | None:
    """GTIN-8/12/13/14 with a valid check digit → 13-digit EAN (GTIN-8 kept as is); anything else → None."""
    d = re.sub(r"\D", "", str(v or ""))
    if len(d) == 12:
        d = "0" + d
    elif len(d) == 14 and d.startswith("0"):
        d = d[1:]
    if len(d) not in (8, 13) or set(d) == {"0"}:
        return None
    body, check = d[:-1], int(d[-1])
    total = sum(int(c) * (3 if i % 2 == 0 else 1) for i, c in enumerate(reversed(body)))
    return d if (10 - total % 10) % 10 == check else None


def title_key(title: str | None, brand: str | None = None) -> str | None:
    toks = sorted({t for t in re.findall(r"[a-z0-9]+", fold(title)) if len(t) > 1 and t not in STOP})
    if len(toks) < 2:
        return None  # too generic to group on
    b = re.sub(r"[^a-z0-9]", "", fold(brand))
    return (f"{b}|" if b else "") + " ".join(toks)[:240]


def tokens(s: str | None) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", fold(s)) if len(t) > 2 and t not in STOP}


def split_keywords(s: str | list | None) -> list[str]:
    """'feng shui bracelet | Glücksarmband, bracelet porte-bonheur' -> folded, deduped keywords (OR'ed in search)."""
    parts = s if isinstance(s, list) else re.split(r"[|,\n;]", s or "")
    return list(dict.fromkeys(fold(k).strip() for k in parts if k and k.strip()))


def _search_text(it: dict) -> str:
    return fold(" ".join(str(x) for x in (it.get("title"), it.get("brand"), it.get("category"), it.get("ean"),
                                          it.get("seller_name")) if x))


# ------------------------------------------------------------------ upsert
def _product_for(db: Session, it: dict) -> EUProduct:
    ean = it.get("ean")
    key = title_key(it.get("title"), it.get("brand"))
    p = None
    if ean:
        p = db.scalar(select(EUProduct).where(EUProduct.ean == ean).limit(1))
    if p is None and key:
        p = db.scalar(select(EUProduct).where(EUProduct.title_key == key, or_(EUProduct.ean.is_(None), EUProduct.ean == ean)).limit(1))
    if p is None:
        p = EUProduct(canonical_title=(it.get("title") or "")[:512], title_key=key, brand=it.get("brand"), category=it.get("category"),
                      ean=ean, image_url=it.get("thumbnail_url"), attributes_json={})
        db.add(p)
        db.flush()
    else:  # fill gaps only — never overwrite a known value with an unknown one
        p.ean = p.ean or ean
        p.image_url = p.image_url or it.get("thumbnail_url")
        p.brand = p.brand or it.get("brand")
        p.category = p.category or it.get("category")
    return p


def _supplier_for(db: Session, source: str, sup: dict) -> EUSupplier:
    s = db.scalar(select(EUSupplier).where(EUSupplier.source == source, EUSupplier.name == sup["name"]))
    if s is None:
        s = EUSupplier(source=source, **{k: sup.get(k) for k in ("name", "supplier_type", "country", "website", "moq",
                                                                "lead_time_days", "vat_note", "access_terms")})
        db.add(s)
        db.flush()
    return s


def upsert_items(db: Session, items: list[dict], query: str | None = None, now: datetime | None = None) -> list[int]:
    """Idempotent: (source, source_product_id, market_country) is the listing identity. Returns listing ids."""
    now = now or datetime.utcnow()
    ids = []
    for raw in items:
        it = dict(raw, ean=norm_ean(raw.get("ean")))
        if not it.get("source") or not it.get("source_product_id") or not it.get("market_country"):
            continue
        row = db.scalar(select(EUListing).where(EUListing.source == it["source"], EUListing.source_product_id == it["source_product_id"],
                                                EUListing.market_country == it["market_country"]))
        if row is None:
            if not it.get("title"):
                continue  # a lookup without a title can only refresh an existing listing
            row = EUListing(source=it["source"], source_product_id=it["source_product_id"], market_country=it["market_country"],
                            title=it["title"][:512], observed_at=now, query=query, raw_metadata_ref={})
            db.add(row)
        for f in ("kind", "seller_id", "seller_name", "brand", "category", "ean", "currency", "price", "shipping_price",
                  "price_includes_vat", "thumbnail_url", "data_access", "confidence"):
            v = it.get(f)
            if v is not None:
                setattr(row, f, v)
        if it.get("title"):
            row.title = it["title"][:512]
        if it.get("product_url"):
            row.url = it["product_url"]
        if it.get("availability") and (it["availability"] != "unknown" or not row.availability):
            row.availability = it["availability"]
        row.last_seen_at = now
        row.raw_metadata_ref = it.get("raw") or row.raw_metadata_ref
        row.search_text = _search_text({**it, "title": row.title, "brand": row.brand, "category": row.category, "ean": row.ean})
        if it.get("supplier"):
            row.supplier_id = _supplier_for(db, it["source"], it["supplier"]).id
        if row.product_id is None:
            row.product_id = _product_for(db, {**it, "title": row.title}).id
        db.flush()
        _snapshot(db, row, now)
        for s in it.get("signals") or []:
            _signal(db, row, s, now)
        ids.append(row.id)
    db.commit()
    return ids


def _snapshot(db: Session, row: EUListing, now: datetime):
    if row.price is None or not row.currency:
        return
    last = db.scalar(select(EUPriceSnapshot).where(EUPriceSnapshot.listing_id == row.id).order_by(EUPriceSnapshot.captured_at.desc()).limit(1))
    if last and last.price == row.price and last.shipping_price == row.shipping_price and last.currency == row.currency \
            and now - last.captured_at < SNAPSHOT_EVERY:
        return
    db.add(EUPriceSnapshot(listing_id=row.id, price=row.price, shipping_price=row.shipping_price, currency=row.currency, captured_at=now))


def _signal(db: Session, row: EUListing, s: dict, now: datetime):
    last = db.scalar(select(EUTrendSignal).where(EUTrendSignal.listing_id == row.id, EUTrendSignal.signal_type == s["signal_type"])
                     .order_by(EUTrendSignal.captured_at.desc()).limit(1))
    if last and last.value == s["value"] and now - last.captured_at < SIGNAL_EVERY:
        return
    db.add(EUTrendSignal(product_id=row.product_id, listing_id=row.id, country=row.market_country, signal_type=s["signal_type"],
                         value=float(s["value"]), sample_size=s.get("sample_size"), source=row.source,
                         confidence=s.get("confidence", 1.0), captured_at=now))


# ------------------------------------------------------------------ FX (ECB reference rates, free + official)
def parse_ecb(xml: str) -> tuple[str, dict[str, float]]:
    root = ET.fromstring(xml)
    day, rates = None, {}
    for el in root.iter():
        if el.tag.endswith("Cube"):
            if el.get("time"):
                day = el.get("time")
            if el.get("currency"):
                rates[el.get("currency")] = float(el.get("rate"))
    if not day or not rates:
        raise ValueError("ECB feed without rates")
    return day, rates


def refresh_fx(db: Session) -> dict:
    r = httpx.get(ECB_URL, timeout=20)
    r.raise_for_status()
    day, rates = parse_ecb(r.text)
    have = set(db.scalars(select(EUFxRate.currency).where(EUFxRate.rate_date == day)).all())
    for cur, rate in rates.items():
        if cur not in have:
            db.add(EUFxRate(currency=cur, rate=rate, rate_date=day))
    db.commit()
    return {"rate_date": day, "currencies": len(rates)}


def fx_table(db: Session) -> dict[str, tuple[float, str]]:
    """Latest ECB rate per currency: {cur: (units per 1 EUR, rate_date)}; EUR itself is (1, None)."""
    out: dict[str, tuple[float, str]] = {"EUR": (1.0, None)}
    for r in db.scalars(select(EUFxRate).order_by(EUFxRate.rate_date)).all():
        out[r.currency] = (r.rate, r.rate_date)
    return out


def to_eur(amount: float | None, cur: str | None, fx: dict) -> dict | None:
    """Reference conversion with its timestamp; None when the rate is unknown (never guessed)."""
    if amount is None or not cur or cur not in fx:
        return None
    rate, day = fx[cur]
    return {"eur": round(amount / rate, 2), "rate": rate, "rate_date": day}


# ------------------------------------------------------------------ search (Product Explorer)
def keyword_filter(kws: list[str]):
    """OR across keywords; inside one keyword every word must appear (accent-folded)."""
    clauses = []
    for k in kws:
        words = [w for w in re.findall(r"[a-z0-9]+", fold(k)) if w]
        if words:
            clauses.append(and_(*[func.coalesce(EUListing.search_text, "").contains(w) for w in words]))
    return or_(*clauses) if clauses else None


def listing_filters(keywords=None, countries=None, sources=None, kind=None, brand=None, seller=None, category=None,
                    price_min=None, price_max=None, days=None, min_confidence=None):
    f = []
    if keywords and (kf := keyword_filter(keywords)) is not None:
        f.append(kf)
    if countries:
        f.append(EUListing.market_country.in_(countries))
    if sources:
        f.append(EUListing.source.in_(sources))
    if kind:
        f.append(EUListing.kind == kind)
    if brand:
        f.append(func.lower(func.coalesce(EUListing.brand, "")).contains(brand.lower()))
    if seller:
        f.append(func.lower(func.coalesce(EUListing.seller_name, "")).contains(seller.lower()))
    if category:
        f.append(func.lower(func.coalesce(EUListing.category, "")).contains(category.lower()))
    if price_min is not None:
        f.append(EUListing.price >= price_min)
    if price_max is not None:
        f.append(EUListing.price <= price_max)
    if days:
        f.append(EUListing.last_seen_at >= datetime.utcnow() - timedelta(days=days))
    if min_confidence is not None:
        f.append(EUListing.confidence >= min_confidence)
    return f


def listing_dict(l: EUListing, fx: dict) -> dict:
    return {"id": l.id, "product_id": l.product_id, "kind": l.kind, "source": l.source, "source_product_id": l.source_product_id,
            "market_country": l.market_country, "title": l.title, "price": l.price, "currency": l.currency,
            "shipping_price": l.shipping_price, "price_includes_vat": l.price_includes_vat,
            "price_eur": to_eur(l.price, l.currency, fx), "seller_id": l.seller_id, "seller_name": l.seller_name,
            "brand": l.brand, "category": l.category, "ean": l.ean, "availability": l.availability or "unknown",
            "product_url": l.url, "thumbnail_url": l.thumbnail_url, "data_access": l.data_access, "confidence": l.confidence,
            "collection_method": "official_api" if l.data_access == "authorized_api" else l.data_access,
            "observed_at": l.observed_at, "last_seen_at": l.last_seen_at, "query": l.query}
