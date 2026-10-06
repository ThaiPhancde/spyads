"""Entity Resolution — "Ad nào thuộc Product nào?" (blueprint §5).

Signals used in this MVP: title similarity (normalized tokens + synonyms),
landing-page / product URL slug, price range, category and brand.
Image/video embedding (pgvector) can be plugged into `_extra_similarity`.
"""
import re
import unicodedata
from difflib import SequenceMatcher
from urllib.parse import urlparse

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Product, ProductAlias

MATCH_THRESHOLD = 0.62

# Words that describe form factor/marketing rather than the product itself
STOPWORDS = {
    "portable", "electric", "electronic", "device", "machine", "new", "best", "pro", "plus", "mini",
    "smart", "original", "premium", "upgraded", "upgrade", "the", "a", "an", "for", "with", "and", "of",
    "2023", "2024", "2025", "2026", "hot", "sale", "official", "kit", "set", "tool", "product",
    "wireless", "cordless", "may", "máy", "chính", "hãng", "cao", "cấp", "loại", "mới", "dụng", "cụ", "bộ", "sản", "phẩm",
}

# Canonical token map — product synonyms across sellers/languages
SYNONYMS = {
    "massage": "massager", "massaging": "massager", "massagers": "massager", "mát": "massager", "xa": "massager",
    "cervical": "neck", "cổ": "neck", "vai": "shoulder",
    "serum": "serum", "essence": "serum",
    "cream": "cream", "creme": "cream", "kem": "cream",
    "acne": "acne", "mụn": "acne", "pimple": "acne",
    "hair": "hair", "tóc": "hair",
    "remover": "remover", "removal": "remover", "trimmer": "trimmer", "shaver": "trimmer",
    "lamp": "light", "led": "light", "light": "light", "đèn": "light",
    "posture": "posture", "corrector": "corrector", "brace": "corrector",
    "blender": "blender", "juicer": "blender", "xay": "blender",
    "vacuum": "vacuum", "cleaner": "cleaner", "hút": "vacuum", "bụi": "dust",
    "pet": "pet", "dog": "pet", "cat": "pet",
    "knee": "knee", "gối": "knee", "foot": "foot", "feet": "foot", "chân": "foot",
    "whitening": "whitening", "trắng": "whitening",
    "bottle": "bottle", "bình": "bottle",
    "pillow": "pillow", "gối_ngủ": "pillow",
    "snoring": "snore", "nasal": "nose", "aspirator": "cleaner", "eyebrow": "brow",
}


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def tokenize(name: str) -> list[str]:
    s = (name or "").lower()
    s = re.sub(r"[^\w\s]", " ", s)
    out = []
    for t in s.split():
        if t.isdigit():
            continue
        t = SYNONYMS.get(t, t)
        if t in STOPWORDS:
            continue
        # crude plural folding
        if len(t) > 4 and t.endswith("s") and not t.endswith("ss"):
            t = t[:-1]
        out.append(t)
    return out


def normalize_name(name: str) -> str:
    return " ".join(sorted(set(tokenize(name))))


def url_slug_tokens(url: str | None) -> set[str]:
    if not url:
        return set()
    path = urlparse(url).path
    return set(tokenize(re.sub(r"[-_/]", " ", path)))


def title_similarity(a: str, b: str) -> float:
    ta, tb = set(tokenize(a)), set(tokenize(b))
    if not ta or not tb:
        return 0.0
    jacc = len(ta & tb) / len(ta | tb)
    contain = len(ta & tb) / min(len(ta), len(tb))
    seq = SequenceMatcher(None, " ".join(sorted(ta)), " ".join(sorted(tb))).ratio()
    return 0.4 * jacc + 0.35 * contain + 0.25 * seq


def price_similarity(p1: float | None, p2: float | None) -> float | None:
    if not p1 or not p2:
        return None
    ratio = min(p1, p2) / max(p1, p2)
    return ratio  # 1.0 same price, 0.5 = 2x apart


def _extra_similarity(candidate: dict, product: Product) -> float | None:
    """Hook for image/video embedding similarity (pgvector). Not available in MVP."""
    return None


def match_score(candidate: dict, product: Product, alias_names: list[str]) -> float:
    names = [product.canonical_name, *alias_names]
    title = max((title_similarity(candidate["name"], n) for n in names), default=0)
    score = title

    slug = url_slug_tokens(candidate.get("landing_url"))
    if slug:
        ptoks = set(tokenize(product.canonical_name))
        if ptoks and len(slug & ptoks) / len(ptoks) >= 0.6:
            score += 0.08

    ps = price_similarity(candidate.get("price"), product.price)
    if ps is not None:
        if ps < 0.4:  # >2.5x price gap → probably different product/tier
            score -= 0.15
        elif ps > 0.75:
            score += 0.04

    cat = candidate.get("category")
    if cat and product.category and cat.lower() != product.category.lower():
        score -= 0.2
    if candidate.get("brand") and product.brand and candidate["brand"].lower() == product.brand.lower():
        score += 0.1

    extra = _extra_similarity(candidate, product)
    if extra is not None:
        score = 0.6 * score + 0.4 * extra
    return max(0.0, min(1.0, score))


def next_product_code(db: Session) -> str:
    max_id = db.scalar(select(func.max(Product.id))) or 0
    return f"PRD_{max_id + 1:07d}"


def find_best_match(db: Session, candidate: dict) -> tuple[Product | None, float]:
    toks = set(tokenize(candidate["name"]))
    if not toks:
        return None, 0.0
    products = db.scalars(select(Product)).all()
    alias_map: dict[int, list[str]] = {}
    for a in db.scalars(select(ProductAlias)).all():
        alias_map.setdefault(a.product_id, []).append(a.name)
    best, best_score = None, 0.0
    for p in products:
        ptoks = set(tokenize(p.canonical_name))
        for n in alias_map.get(p.id, []):
            ptoks |= set(tokenize(n))
        if not (toks & ptoks):  # blocking: must share at least one canonical token
            continue
        s = match_score(candidate, p, alias_map.get(p.id, []))
        if s > best_score:
            best, best_score = p, s
    return best, best_score


def resolve_product(db: Session, candidate: dict, source: str | None = None) -> tuple[Product, bool, float]:
    """candidate = {name, price?, category?, brand?, landing_url?, country?, currency?}.

    Returns (product, created, score). Existing → attach alias. New → create canonical product.
    """
    product, score = find_best_match(db, candidate)
    created = False
    if product is None or score < MATCH_THRESHOLD:
        product = Product(
            product_code=next_product_code(db),
            canonical_name=candidate["name"].strip().title(),
            category=candidate.get("category"),
            subcategory=candidate.get("subcategory"),
            brand=candidate.get("brand"),
            price=candidate.get("price"),
            currency=candidate.get("currency"),
            market=candidate.get("country"),
            country=candidate.get("country"),
            image_url=candidate.get("image_url"),
        )
        db.add(product)
        db.flush()
        created, score = True, 1.0
    else:
        if not product.category and candidate.get("category"):
            product.category = candidate["category"]
        if not product.price and candidate.get("price"):
            product.price = candidate["price"]

    existing = {a.name.lower() for a in product.aliases}
    if candidate["name"].lower() not in existing and candidate["name"].lower() != product.canonical_name.lower():
        db.add(ProductAlias(product_id=product.id, name=candidate["name"], source=source, match_score=round(score, 3)))
    return product, created, score


def merge_products(db: Session, source: Product, target: Product):
    """Manual correction: merge `source` into `target` (all child rows re-pointed)."""
    from ..models import Ad, Comment, Experiment, Order, ProductDailySnapshot, Store, CreativeDailySnapshot, Alert, LifecycleEvent

    for model in (Ad, Comment, Experiment, Order, Store, Alert):
        for row in db.scalars(select(model).where(model.product_id == source.id)).all():
            row.product_id = target.id
    for model in (ProductDailySnapshot, CreativeDailySnapshot, LifecycleEvent):
        for row in db.scalars(select(model).where(model.product_id == source.id)).all():
            db.delete(row)
    db.add(ProductAlias(product_id=target.id, name=source.canonical_name, source="manual_merge", match_score=1.0))
    for a in list(source.aliases):
        a.product = target
    db.flush()
    db.delete(source)
