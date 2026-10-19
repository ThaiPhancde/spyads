"""Entity Resolution — "Ad nào thuộc Product nào?" (blueprint §5).

Signals used in this MVP: title similarity (normalized tokens + synonyms),
landing-page / product URL slug, price range, category and brand.
Image/video embedding (pgvector) can be plugged into `_extra_similarity`.
"""
import re
from difflib import SequenceMatcher
from functools import lru_cache
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
    if min(len(ta), len(tb)) <= 2:  # "Back Brace" used to absorb 45 aliases: a 1-2 token name is contained in everything
        contain *= 0.5
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
    from .product_identity import identity_match, compatible_titles
    ident = identity_match(candidate.get("identity"), getattr(product, "product_identity", None))
    if ident is False:
        return 0.0
    if ident is True:
        return 1.0
    if not compatible_titles(candidate["name"], product.canonical_name):
        return 0.0
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


@lru_cache(maxsize=100_000)
def _toks(name: str) -> frozenset[str]:
    return frozenset(tokenize(name))


# token → product ids, built once per process and topped up with rows added since (by id)
# ponytail: renamed products keep their old tokens until restart — fine while names are only set at creation / merge
_idx = {"pid": 0, "aid": 0, "names": {}, "inv": {}}


def _index(db: Session) -> dict:
    def add(pid: int, name: str):
        _idx["names"].setdefault(pid, []).append(name)
        for t in _toks(name):
            _idx["inv"].setdefault(t, set()).add(pid)

    for pid, name, status in db.execute(select(Product.id, Product.canonical_name, Product.name_status).where(Product.id > _idx["pid"])):
        if status != "UNKNOWN":  # "Chưa xác định sản phẩm" placeholders must never attract real names
            add(pid, name)
        _idx["pid"] = max(_idx["pid"], pid)
    for aid, pid, name in db.execute(select(ProductAlias.id, ProductAlias.product_id, ProductAlias.name)
                                     .where(ProductAlias.id > _idx["aid"])):
        add(pid, name)
        _idx["aid"] = max(_idx["aid"], aid)
    return _idx


def find_best_match(db: Session, candidate: dict) -> tuple[Product | None, float]:
    from sqlalchemy import String, cast, or_
    from .product_identity import identity_match
    ident = candidate.get("identity") or {}
    if ident.get("gtin") or ident.get("brand") and ident.get("sku"):
        values = [ident[k] for k in ("gtin", "sku") if ident.get(k)]
        hits = [p for p in db.scalars(select(Product).where(or_(*[cast(Product.product_identity, String).contains(v) for v in values])).limit(25))
                if identity_match(ident, p.product_identity) is True]
        if len(hits) == 1:
            return hits[0], 1.0
    toks = set(tokenize(candidate["name"]))
    if not toks:
        return None, 0.0
    idx = _index(db)

    def pre(name: str) -> float:  # cheap token overlap (jaccard + containment) — the bulk of title_similarity
        t = _toks(name)
        common = len(toks & t)
        return common / len(toks | t) + common / min(len(toks), len(t)) if common else 0.0

    pids = set().union(*(idx["inv"].get(t, ()) for t in toks))
    scored = sorted(((max((pre(n) for n in idx["names"].get(pid, ())), default=0.0), pid) for pid in pids), reverse=True)
    scored = [x for x in scored if x[0] > 0]  # dropped / unindexed products have no names left
    # ponytail: full scoring (difflib) only for the 25 best token matches — a generic word like "bracelet" used to send
    # thousands of products through it (~1 s / ad). Raise the cap if matches get missed.
    cand = [pid for _, pid in scored[:25]]
    alias_map: dict[int, list[str]] = {}
    for pid, name in db.execute(select(ProductAlias.product_id, ProductAlias.name).where(ProductAlias.product_id.in_(cand))) if cand else []:
        alias_map.setdefault(pid, []).append(name)
    best, best_score = None, 0.0
    # light rows (the fields match_score reads) — the full Product with its JSON columns is loaded for the winner only
    for p in db.execute(select(Product.id, Product.canonical_name, Product.price, Product.category, Product.brand, Product.product_identity)
                        .where(Product.id.in_(cand))) if cand else []:
        s = match_score(candidate, p, alias_map.get(p.id, []))
        if s > best_score:
            best, best_score = p.id, s
    return (db.get(Product, best) if best else None), best_score


JUNK_NAME = re.compile(r"[₱$€£¥]\s?\d[\d.,]*|\d[\d.,]*\s?(php|sar|aed|usd|kwd|egp|aud|gbp|eur|đ)\b|\d{1,2}\s?%\s?(off|discount|giảm)?"
                       r"|\b(sale|discount|promo|free ?shipping|cod|buy now|order now|giảm giá|freeship|only|lang|فقط)\b", re.I)


BUCKET_NAME = re.compile(r"\(chưa đặt tên\)\s*$| creative$", re.I)  # placeholder names from before services/naming.py


def clean_name(name: str | None, advertiser: str | None = None) -> str | None:
    """Ad-copy junk is not a product name: price / discount fragments are stripped, the advertiser's own name is rejected."""
    n = re.sub(r"\s+", " ", JUNK_NAME.sub(" ", name or "")).strip(" -,.|:!")
    if len(n) < 3 or n.lower() == (advertiser or "").strip().lower():
        return None
    return n


def _pretty(name: str) -> str:
    """Title-case lower / UPPER-case names only — "iPhone 16 Case" and "TRON ANC" keep their casing."""
    return name.title() if name == name.lower() or name == name.upper() else name


def resolve_product(db: Session, candidate: dict, source: str | None = None) -> tuple[Product, bool, float]:
    """candidate = {name, naming?, price?, category?, brand?, landing_url?, country?, currency?, advertiser?, ad_id?}.
    `naming` = services.naming.best() of the ad; without it the name is judged as a source-reported one ("adapter").

    UNKNOWN (no evidence) → a product of its own, never matched: grouping unidentified ads by shop mixed unrelated
    products ("Unknown (chưa đặt tên)" held 938 TikTok ads). Returns (product, created, score)."""
    from . import naming as N

    adv = candidate.get("advertiser")
    nm = candidate.get("naming")
    if nm is None:
        raw = candidate.get("name") or ""
        name = None if BUCKET_NAME.search(raw) or N.is_bad_name(raw, adv) else clean_name(raw, adv)
        nm = N.result(name, "adapter") if name else N.result(None, None)
    name = clean_name(nm["name"], adv) if nm["name"] else None
    if not name:
        nm = N.result(None, None)
    evidence = [{**e, "ad_id": candidate.get("ad_id")} for e in nm.get("evidence") or []]
    ident = identity_product(db, candidate.get("fingerprint"), candidate.get("landing_url"), candidate.get("copy"), candidate.get("ad_id"))
    if ident:  # same product page / same creative as an earlier ad: that product, whatever the names say
        product, score = db.get(Product, ident), 1.0
        from .product_identity import identity_match
        if identity_match(candidate.get("identity"), product.product_identity) is False:
            product, score = None, 0.0
    else:
        product, score = (None, 0.0) if not name and not candidate.get("identity") else find_best_match(db, {**candidate, "name": name or ""})
    created = False
    if product is None or score < MATCH_THRESHOLD:
        product = Product(
            product_code=next_product_code(db),
            canonical_name=_pretty(name) if name else N.UNKNOWN_LABEL,
            category=candidate.get("category"),
            subcategory=candidate.get("subcategory"),
            brand=candidate.get("brand"),
            product_identity=candidate.get("identity") or None,
            price=candidate.get("price"),
            currency=candidate.get("currency"),
            market=candidate.get("country"),
            country=candidate.get("country"),
            image_url=candidate.get("image_url"),
            name_status=nm["status"], name_source=nm["source"], name_confidence=nm["confidence"], name_evidence=evidence[:6],
        )
        db.add(product)
        db.flush()
        return product, True, 1.0
    if not product.category and candidate.get("category"):
        product.category = candidate["category"]
    if not product.price and candidate.get("price"):
        product.price = candidate["price"]
    N.absorb(product, nm, evidence)
    if candidate.get("identity"):
        product.product_identity = dict(product.product_identity or {}) | candidate["identity"]
    existing = {a.name.lower() for a in product.aliases}
    if name and name.lower() not in existing and name.lower() != product.canonical_name.lower():
        db.add(ProductAlias(product_id=product.id, name=name, source=source, match_score=round(score, 3)))
    return product, created, score


# ------------------------------------------------------------ identity (not name) signals
# Same product page, same copy from the same page, same picture = same product, whatever the name says. A shop's home
# page, a social / chat / app-store link or a collection page is the advertiser, not a product — never grouped on.
_NO_IDENTITY_HOST = re.compile(r"(^|\.)(facebook\.com|fb\.com|fb\.me|instagram\.com|tiktok\.com|m\.me|wa\.me|whatsapp\.com|"
                               r"messenger\.com|play\.google\.com|apps\.apple\.com|itunes\.apple\.com|onelink\.me|linktr\.ee|"
                               r"beacons\.ai|bit\.ly|google\.com|youtube\.com|doubleclick\.net|adjust\.com|app\.link)$", re.I)
_NO_IDENTITY_PATH = re.compile(r"^/?$|/(collections?|shop|store|category|categories|pages/(contact|about)|search)(/|$)", re.I)
TRIVIAL_PHASH = {"0000000000000000", "ffffffffffffffff", "8000000000000000"}


def landing_key(url: str | None) -> str | None:
    """host + path of a product page (query dropped), or None when the link says nothing about which product."""
    if not url:
        return None
    u = urlparse(url if "://" in url else f"https://{url}")
    host, path = u.netloc.lower().removeprefix("www."), u.path.rstrip("/").lower()
    if not host or _NO_IDENTITY_HOST.search(host) or _NO_IDENTITY_PATH.search(path or "/"):
        return None
    return f"{host}{path}"


def identity_product(db: Session, fingerprint: str | None, landing: str | None, copy: str | None,
                     exclude_ad: int | None = None) -> int | None:
    """Product of an earlier ad that is evidently the same thing: same product page, or same copy + page + link
    (ingest.cross_source_key). Named products first. Copy shorter than 20 chars is not an identity ("Shop now")."""
    from ..models import Ad

    conds = []
    if fingerprint and len((copy or "").strip()) >= 20:
        conds.append(Ad.creative_fingerprint == fingerprint)
    if lk := landing_key(landing):
        conds.append(func.lower(Ad.landing_url).like(f"%{lk}%"))
    if not conds:
        return None
    from sqlalchemy import or_

    rows = db.execute(select(Ad.product_id, Ad.landing_url, Ad.creative_fingerprint, Product.name_status)
                      .join(Product, Product.id == Ad.product_id).where(or_(*conds), Ad.id != (exclude_ad or -1)).limit(50)).all()
    rows = [r for r in rows if (fingerprint and r.creative_fingerprint == fingerprint) or (lk and landing_key(r.landing_url) == lk)]
    named = {r.product_id for r in rows if r.name_status != "UNKNOWN"}
    if len(named) == 1:
        return named.pop()
    if named:  # the page already maps to several named products: ambiguous, let the name decide
        return None
    return min((r.product_id for r in rows), default=None)


def unindex(pid: int) -> None:
    """Forget a product's names (it is being renamed / emptied): it stops attracting matches."""
    _idx["names"].pop(pid, None)


def index_name(pid: int, name: str) -> None:
    """A product renamed after the index was built (MKT edit, naming upgrade) becomes findable under its new name."""
    if _idx["names"] or _idx["pid"]:
        _idx["names"].setdefault(pid, []).append(name)
        for t in _toks(name):
            _idx["inv"].setdefault(t, set()).add(pid)


def move_ads(db: Session, ad_ids: list[int], target_id: int) -> set[int]:
    """Re-point ads + their creatives / comments / stores to `target_id`. Returns the products they came from."""
    from sqlalchemy import update

    from ..models import Ad, Comment, Creative, Store

    if not ad_ids:
        return set()
    old = set(db.scalars(select(Ad.product_id).where(Ad.id.in_(ad_ids)).distinct())) - {None, target_id}
    db.execute(update(Ad).where(Ad.id.in_(ad_ids)).values(product_id=target_id))
    db.execute(update(Creative).where(Creative.ad_id.in_(ad_ids)).values(product_id=target_id))
    db.execute(update(Comment).where(Comment.ad_id.in_(ad_ids)).values(product_id=target_id))
    store_ids = select(Ad.store_id).where(Ad.id.in_(ad_ids), Ad.store_id.is_not(None))
    db.execute(update(Store).where(Store.id.in_(store_ids), Store.product_id.in_(old)).values(product_id=target_id))
    db.flush()
    from . import shop_naming

    shop_naming.refresh(db, [*old, target_id])
    return old


def drop_if_empty(db: Session, pid: int) -> bool:
    """Delete a product left without ads (and without MKT votes), child rows first — by the live schema, not the models."""
    from sqlalchemy import inspect, text

    from ..models import Ad, Vote
    from ..cluster_models import ProductCluster

    # Phase 1 cluster IDs preserve original products and their history. Legacy naming/split
    # paths must not cascade-delete that history behind the cluster overlay.
    if db.get(ProductCluster, pid):
        return False

    if db.scalar(select(Ad.id).where(Ad.product_id == pid).limit(1)) or db.scalar(select(Vote.id).where(Vote.product_id == pid).limit(1)):
        return False
    db.flush()
    if (obj := db.get(Product, pid)) is not None:
        db.expunge(obj)  # deleted with SQL below: the session must not flush it again
    insp = inspect(db.connection())
    for t in insp.get_table_names():
        for fk in insp.get_foreign_keys(t):
            if fk["referred_table"] == "products" and t != "products":
                db.execute(text(f'delete from "{t}" where "{fk["constrained_columns"][0]}" = :pid'), {"pid": pid})
    db.execute(text("delete from products where id = :pid"), {"pid": pid})
    _idx["names"].pop(pid, None)
    return True


def merge_products(db: Session, source: Product, target: Product, by: str | None = None):
    """Manual correction: merge `source` into `target` (all child rows re-pointed). Undo: split_ads with the event's ad ids."""
    from ..models import Ad, Alert, Event, LifecycleEvent, ProductDailySnapshot, Store, Vote

    ad_ids = list(db.scalars(select(Ad.id).where(Ad.product_id == source.id)))
    db.add(Event(type="PRODUCT_MERGED", product_id=target.id, data={
        "source_id": source.id, "source_code": source.product_code, "source_name": source.canonical_name,
        "source_name_vi": source.name_vi, "source_status": source.name_status, "ad_ids": ad_ids, "by": by}))
    move_ads(db, ad_ids, target.id)
    for model in (Store, Alert, Vote):
        for row in db.scalars(select(model).where(model.product_id == source.id)).all():
            row.product_id = target.id
    from ..cluster_models import ProductCluster
    if not db.get(ProductCluster, source.id):
        for model in (ProductDailySnapshot, LifecycleEvent):
            for row in db.scalars(select(model).where(model.product_id == source.id)).all():
                db.delete(row)
    if source.name_status != "UNKNOWN":
        db.add(ProductAlias(product_id=target.id, name=source.canonical_name, source="manual_merge", match_score=1.0))
    for a in list(source.aliases):
        a.product = target
    db.flush()
    drop_if_empty(db, source.id)


def split_ads(db: Session, product: Product, ad_ids: list[int], name: str | None = None, by: str | None = None,
              name_vi: str | None = None) -> Product:
    """Move some ads of `product` to a new product (MKT-named → VERIFIED, else UNKNOWN). Logged for undo."""
    from ..models import Ad, Event
    from . import naming as N

    ids = list(db.scalars(select(Ad.id).where(Ad.id.in_(ad_ids), Ad.product_id == product.id)))
    if not ids:
        raise ValueError("no ads of this product selected")
    new = Product(product_code=next_product_code(db), canonical_name=name or N.UNKNOWN_LABEL, category=product.category,
                  country=product.country, market=product.market, currency=product.currency,
                  name_status="UNKNOWN", name_evidence=[])
    db.add(new)
    db.flush()
    if name or name_vi:
        N.set_name(new, name, name_vi, by)
    move_ads(db, ids, new.id)
    db.add(Event(type="PRODUCT_SPLIT", product_id=product.id, data={"to_id": new.id, "ad_ids": ids, "by": by}))
    return new
