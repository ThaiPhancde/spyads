"""Provider identity is independent of the ad network and the transport adapter."""
import re

BRANDS = {
    "pipiads": {"label": "Pipiads", "color": "#11A683", "website": "https://www.pipiads.com/"},
    "minea": {"label": "Minea", "color": "#E85F2E", "website": "https://www.minea.com/"},
}


def source_key(value):
    value = str(value or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9_]+", value):
        raise ValueError("Tên nguồn chỉ gồm chữ thường, số và dấu gạch dưới")
    return value


def identity(adapter, config=None):
    source = str((config or {}).get("source") or "").strip().lower()
    brand = BRANDS.get(source)
    if adapter not in ("export", "http", "apify_actor") or not brand:
        return None
    mode = {"export": "Nhập file CSV/XLSX/JSON", "http": "API doanh nghiệp", "apify_actor": "Apify"}[adapter]
    return {"key": f"{source}:{adapter}", "provider": source, "label": brand["label"], "color": brand["color"],
            "name": f"{brand['label']} · {mode}", "website": brand["website"], "mode": mode}
