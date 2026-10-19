"""Allegro import: apps/api/data/imports/allegro.json (written by Grok Bot) -> eu_listings via service.upsert_items.

No connector, no network: the file is the only input. A missing / unreadable / empty file is reported as such — nothing
is ever invented. Identity = (source "allegro", offer_id, "PL"), so re-importing the same file never duplicates
(upsert_items updates price / seller / last_seen and records a price snapshot only when the price changed).
Accepted shapes: a JSON list, or an object holding the list under items / offers / products / listings / results / data.
Field names are matched loosely because the bot's schema is not fixed yet; rows without an offer id or title are skipped
and counted.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from .connectors.base import listing
from .service import upsert_items

SOURCE, COUNTRY = "allegro", "PL"
PATH = Path(__file__).resolve().parents[2] / "data" / "imports" / "allegro.json"
LIST_KEYS = ("items", "offers", "products", "listings", "results", "data")
AVAIL = {"in_stock": "in_stock", "instock": "in_stock", "available": "in_stock", "out_of_stock": "out_of_stock",
         "outofstock": "out_of_stock", "unavailable": "out_of_stock"}


def _first(d: dict, *keys):
    for k in keys:
        v = d.get(k)
        if v not in (None, "", [], {}):
            return v
    return None


def _num(v) -> float | None:
    """12.99 | "12,99 zł" | "1 299,00" | {"amount": "12.99"} -> float (None when it isn't a positive number)."""
    if isinstance(v, dict):
        v = _first(v, "amount", "value", "price")
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        n = float(v)
    else:
        s = re.sub(r"[^\d,.\-]", "", str(v))
        if "," in s and "." in s:
            s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
        else:
            s = s.replace(",", ".")
        try:
            n = float(s)
        except ValueError:
            return None
    return n if n > 0 else None


def _url(v) -> str | None:
    if isinstance(v, dict):
        v = _first(v, "url", "src", "href")
    if isinstance(v, list):
        v = _url(v[0]) if v else None
    return v.strip() if isinstance(v, str) and urlparse(v.strip()).scheme in ("http", "https") else None


def map_item(r: dict) -> dict | None:
    """One raw bot row -> normalized listing dict, or None when it has no offer id / title (never guessed)."""
    if not isinstance(r, dict):
        return None
    oid = _first(r, "offer_id", "offerId", "id", "allegro_id")
    title = _first(r, "title", "name")
    if oid is None or not isinstance(title, str) or not title.strip():
        return None
    oid = str(oid).strip()
    price_raw = _first(r, "price", "price_pln", "amount", "buy_now_price")
    cur = _first(r, "currency") or (price_raw.get("currency") if isinstance(price_raw, dict) else None) or "PLN"
    seller = _first(r, "seller", "seller_name", "seller_login", "shop")
    seller_id = _first(r, "seller_id", "sellerId") or (seller.get("id") if isinstance(seller, dict) else None)
    if isinstance(seller, dict):
        seller = _first(seller, "name", "login")
    url = _url(_first(r, "url", "link", "offer_url", "product_url")) or f"https://allegro.pl/oferta/{oid}"
    stock = str(_first(r, "availability", "stock_status") or "").lower().replace(" ", "_")
    return listing(
        source=SOURCE, source_product_id=oid, market_country=COUNTRY, title=title.strip(), price=_num(price_raw),
        currency=str(cur).upper()[:8], shipping_price=_num(_first(r, "shipping_price", "delivery_price", "shipping")),
        price_includes_vat=True,
        thumbnail_url=_url(_first(r, "thumbnail_url", "thumbnail", "image_url", "image", "images")), product_url=url,
        seller_id=str(seller_id) if seller_id else None, seller_name=str(seller).strip()[:255] if seller else None,
        ean=_first(r, "ean", "gtin"), brand=_first(r, "brand", "manufacturer"), category=_first(r, "category"),
        availability=AVAIL.get(stock, "unknown"), data_access="imported_json", confidence=0.7,
        raw={"import": "allegro.json", "row": {k: v for k, v in r.items() if k != "html"}},
    )


def status(path: Path = PATH) -> dict:
    """File state for the UI: exists? how many rows? when written? Never reads more than it must."""
    if not path.exists():
        return {"state": "no_data", "path": "apps/api/data/imports/allegro.json", "message": "Chưa có file allegro.json — chưa có dữ liệu Allegro."}
    st = path.stat()
    out = {"state": "file_found", "path": "apps/api/data/imports/allegro.json", "bytes": st.st_size,
           "modified_at": datetime.utcfromtimestamp(st.st_mtime)}
    try:
        out["rows"] = len(read_rows(path))
    except ValueError as e:
        out.update(state="invalid_file", message=str(e))
    return out


def read_rows(path: Path = PATH) -> list:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as e:
        raise ValueError(f"allegro.json không đọc được: {e}") from e
    if isinstance(data, dict):
        data = next((data[k] for k in LIST_KEYS if isinstance(data.get(k), list)), None)
    if not isinstance(data, list):
        raise ValueError("allegro.json phải là một mảng, hoặc object có mảng trong items/offers/products/listings/results/data")
    return data


def import_file(db: Session, path: Path = PATH) -> dict:
    """Idempotent import. Raises FileNotFoundError / ValueError for the router to turn into 404 / 400."""
    if not path.exists():
        raise FileNotFoundError(str(path))
    rows = read_rows(path)
    items = [m for m in (map_item(r) for r in rows) if m]
    seen, dupes = set(), 0
    uniq = []
    for m in items:
        if m["source_product_id"] in seen:  # same offer twice in one file: last row wins
            dupes += 1
            uniq = [u for u in uniq if u["source_product_id"] != m["source_product_id"]]
        seen.add(m["source_product_id"])
        uniq.append(m)
    ids = upsert_items(db, uniq, query=None)
    return {"rows": len(rows), "valid": len(uniq), "skipped_invalid": len(rows) - len(items), "duplicates_in_file": dupes,
            "written": len(ids), "listing_ids": ids}
