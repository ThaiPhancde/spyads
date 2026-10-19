"""Cheap grounded identity gates shared by clustering, naming and price matching."""
import re


def normalize(value):
    return re.sub(r"[^\w]", "", str(value or "").casefold())


def valid_gtin(value):
    value = str(value or "").strip()
    if not value.isdigit() or len(value) not in (8, 12, 13, 14):
        return False
    check = sum(int(n) * (3 if i % 2 == 0 else 1) for i, n in enumerate(reversed(value[:-1])))
    return (10 - check % 10) % 10 == int(value[-1])


def metadata(record):
    raw = record.raw_source or {}
    nested = raw.get("product") if isinstance(raw.get("product"), dict) else {}
    raw = raw | nested
    out = {}
    for key, aliases in {"brand": ("brand",), "sku": ("sku", "mpn"), "gtin": ("gtin", "ean", "gtin13"),
                         "model": ("model",), "title": ("product_title", "og_title")}.items():
        value = getattr(record, key, None) or next((raw[k] for k in aliases if raw.get(k)), None)
        if isinstance(value, str) and value.strip():
            out[key] = value.strip()[:160]
    return out


def identity_match(left, right):
    """True = strong identity, False = explicit conflict, None = insufficient metadata.
    Retailer-local SKU alone is never a cross-shop identity.
    """
    left, right = left or {}, right or {}
    for k in ("gtin", "brand", "model"):
        if left.get(k) and right.get(k) and normalize(left[k]) != normalize(right[k]):
            return False
    if left.get("gtin") and right.get("gtin") and valid_gtin(left["gtin"]) and valid_gtin(right["gtin"]):
        return True
    if left.get("brand") and right.get("brand") and left.get("sku") and right.get("sku"):
        return normalize(left["sku"]) == normalize(right["sku"])
    return None


def numeric_models(text):
    """Keep model/size identifiers: iPhone 15 and 16, 128 GB and 256 GB are not interchangeable."""
    return set(re.findall(r"\b(?:[a-z]+[-]?)?\d+[a-z]*\b", (text or "").casefold()))


def compatible_titles(query, title):
    left, right = numeric_models(query), numeric_models(title)
    return not left or left <= right
