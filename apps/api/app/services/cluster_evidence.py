"""Cheap, conservative identity evidence reused by the background clustering pass."""
from collections import defaultdict
from itertools import combinations
import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit

from .entity_resolution import landing_key
from .product_identity import normalize, numeric_models, valid_gtin


def product_url(url):
    if not landing_key(url):
        return None
    u = urlsplit(url if "://" in url else "https://" + url)
    # Variant/SKU/product query parameters are identity; strip ONLY known tracking parameters.
    params = sorted((k, v) for k, v in parse_qsl(u.query, keep_blank_values=True)
                    if not k.lower().startswith("utm_") and k.lower() not in {"fbclid", "gclid", "ttclid", "msclkid"})
    return u.netloc.lower().removeprefix("www.") + u.path.rstrip("/") + ("?" + urlencode(params) if params else "")


def normalized_name(name):
    # Keep numeric models, sizes, Pro/Plus/Mini: stripping these silently merges variants.
    return " ".join(re.findall(r"\w+", unicodedata.normalize("NFKC", name or "").casefold()))


def metadata_values(items):
    out = defaultdict(set)
    aliases = {"gtin": ("gtin", "ean", "gtin13"), "brand": ("brand",), "model": ("model", "mpn"),
               "sku": ("sku",), "size": ("size",), "color": ("color", "colour"),
               "capacity": ("capacity", "storage"), "variant": ("variant", "variant_id")}
    for item in items:
        if not isinstance(item, dict):
            continue
        for key, names in aliases.items():
            for alias in names:
                raw = item.get(alias)
                if isinstance(raw, (str, int)) and str(raw).strip():
                    value = normalize(raw)
                    if key == "gtin":
                        if not valid_gtin(str(raw)):
                            continue
                        value = str(raw).zfill(14)
                    if value:
                        out[key].add(value)
    return dict(out)


def conflicts(px, py):
    a, b = px.get("metadata", {}), py.get("metadata", {})
    out = []
    for key in ("gtin", "brand", "model", "size", "color", "capacity", "variant"):
        av, bv = a.get(key, set()), b.get(key, set())
        if len(av) > 1 or len(bv) > 1:
            out.append("mixed_metadata")
        elif av and bv and av != bv:
            out.append("metadata_conflict:" + key)
    # Retailer SKU has meaning only within the same shop/source scope.
    for scope in px.get("skus", {}).keys() & py.get("skus", {}).keys():
        if px["skus"][scope] != py["skus"][scope]:
            out.append("metadata_conflict:sku")
    if px["status"] != "UNKNOWN" and py["status"] != "UNKNOWN":
        na, nb = numeric_models(px["name"]), numeric_models(py["name"])
        if na and nb and na != nb:
            out.append("model_number_conflict")
        va = set(normalized_name(px["name"]).split()) & {"pro", "plus", "mini", "max", "ultra"}
        vb = set(normalized_name(py["name"]).split()) & {"pro", "plus", "mini", "max", "ultra"}
        if va != vb:
            out.append("variant_name_conflict")
    return sorted(set(out))


def add_metadata_evidence(st, sig):
    keys = defaultdict(set)
    for pid, p in st.prods.items():
        if not p["n_ads"]:
            continue
        md = p.get("metadata", {})
        for gtin in md.get("gtin", ()):
            keys[("gtin", gtin)].add(pid)
        if len(md.get("brand", ())) == 1 and len(md.get("model", ())) == 1:
            keys[("brand_model", next(iter(md["brand"])), next(iter(md["model"])))].add(pid)
        for scope, skus in p.get("skus", {}).items():
            for sku in skus:
                keys[("scoped_sku", scope, sku)].add(pid)
    for key, pids in keys.items():
        # Linear star for high-frequency identifiers; no quadratic all-catalogue scan.
        ordered = sorted(pids)
        pairs = combinations(ordered, 2) if len(ordered) <= 40 else ((ordered[0], p) for p in ordered[1:])
        for a, b in pairs:
            s = sig(st, a, b)
            s.setdefault("identity", [])
            if key[0] not in s["identity"]:
                s["identity"].append(key[0])
