"""Phase 6: similar products / ads, "based on your history", "more from followed advertisers".

Two stages on purpose: a bounded SQL prefilter (same category, shared multilingual lexicon concept in `search_text`,
same cluster / advertiser, and pgvector HNSW neighbours on PostgreSQL), then similarity only on that set from data that
is already stored — CLIP vectors, normalised concept keywords, name tokens, pHash. No inference, no AI.

Similar ≠ duplicate: a candidate with *identity* evidence (Phase 1 duplicate link, same product URL, near-identical
image hash with matching name) is returned under `duplicates`, never as a "similar" alternative. Products already in the
same cluster are the same product and are left out.
"""
import os
import re
import time

import numpy as np
from sqlalchemy import func, or_, select, text, true

from .allocation import AllocationLock, ProductAllocation
from .cluster_models import DuplicateAdLink
from .models import Ad, Advertiser, Creative, Product, ProductLink
from .services import lexicon

PRODUCT_W = {"image": 0.35, "concepts": 0.30, "category": 0.20, "tokens": 0.15}
AD_W = {"image": 0.45, "text": 0.40, "phash": 0.15}
STOP = {"the", "and", "for", "with", "your", "you", "our", "new", "free", "shipping", "sale", "best", "now", "get", "buy",
        "order", "today", "only", "off", "cho", "của", "và", "các", "này", "với", "mua", "ngay", "giá", "sản", "phẩm"}


def env(name, default):
    return float(os.getenv(name, default))


def migrate(bind=None):
    """Idempotent, additive: trigram GIN index so the multilingual LIKE prefilter on ads.search_text uses an index.
    PostgreSQL only; skipped (search still works, just slower) when pg_trgm can't be enabled."""
    from .db import engine
    eng = bind or engine
    if eng.dialect.name != "postgresql":
        return False
    try:
        with eng.begin() as c:
            c.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
            c.execute(text("CREATE INDEX IF NOT EXISTS ix_ads_search_text_trgm ON ads USING gin (search_text gin_trgm_ops)"))
        return True
    except Exception:  # noqa: BLE001 — missing privilege / extension: keep running without the index
        import logging
        logging.getLogger("app.similar").warning("pg_trgm index not created; similar prefilter falls back to a scan")
        return False


# ---------------- text ----------------

def tokens(s):
    return {t for t in re.findall(r"[^\W\d_]{3,}", lexicon.fold(s or "")) if t not in STOP}


def concepts(s):
    return {c for _, c in lexicon.concepts_in(s or "")}


def jaccard(a, b):
    return len(a & b) / len(a | b) if a and b else 0.0


def concept_score(a, b):
    """Shared product noun (lexicon 'T' concept) is strong evidence of the same use; otherwise plain overlap."""
    if not a or not b:
        return 0.0
    shared = a & b
    noun = any(lexicon.CONCEPTS[c][0] == "T" for c in shared)
    return max(jaccard(a, b), 0.8 if noun else 0.0)


def concept_terms(cs, toks=(), limit=24):
    """Every language's variant of each concept (an Arabic ad must match an English source), then the most distinctive
    name tokens for products the lexicon doesn't know."""
    out = [t for c in cs for t in lexicon.variants(c) if len(t) >= 3]
    out += sorted((t for t in toks if len(t) >= 4), key=len, reverse=True)[:4]
    return list(dict.fromkeys(out))[:limit]


# ---------------- stored image evidence ----------------

def _image_rows(db, key_col, keys):
    """One stored image per key (lowest image id): (key, image_id, vector, phash)."""
    from .visual import embed
    from .visual.models import ProductImage
    if not keys:
        return {}
    first = (select(key_col.label("k"), func.min(ProductImage.id).label("img"))
             .join(ProductImage, (ProductImage.source == "creative") & (ProductImage.source_id == Creative.id))
             .where(key_col.in_(list(keys)), ProductImage.processing_status == "done")
             .group_by(key_col).subquery())
    out = {}
    for k, img, blob, ph in db.execute(select(first.c.k, first.c.img, ProductImage.embedding, ProductImage.phash)
                                       .join(ProductImage, ProductImage.id == first.c.img)):
        v = None
        if blob:
            v = embed.from_bytes(blob).astype(np.float32)
            n = float(np.linalg.norm(v))
            v = v / n if n else None
        out[k] = (img, v, ph)
    return out


def cos_score(a, b, lo=0.75, hi=0.95):
    if a is None or b is None:
        return 0.0
    return min(1.0, max(0.0, (float(np.dot(a, b)) - lo) / (hi - lo)))


def hamming(a, b):
    try:
        return bin(int(a, 16) ^ int(b, 16)).count("1")
    except (TypeError, ValueError):
        return 64


def pg_neighbours(db, image_id, k=200, floor=0.80):
    """pgvector HNSW neighbours of one stored image → image ids. Empty off PostgreSQL / without the column."""
    from .visual import embed
    if image_id is None or not embed.pgvector_ready(db):
        return []
    db.execute(text("SET LOCAL hnsw.ef_search = 300"))
    rows = db.execute(text("SELECT n.id FROM product_images a CROSS JOIN LATERAL ("
                           " SELECT b.id, 1 - (b.embedding_vec <=> a.embedding_vec) AS cos FROM product_images b"
                           " WHERE b.id <> a.id AND b.embedding_vec IS NOT NULL AND b.processing_status = 'done'"
                           " ORDER BY b.embedding_vec <=> a.embedding_vec LIMIT :k) n"
                           " WHERE a.id = :i AND a.embedding_vec IS NOT NULL AND n.cos >= :floor"),
                      {"i": image_id, "k": k, "floor": floor}).scalars().all()
    return list(rows)


def _creatives_of_images(db, image_ids):
    from .visual.models import ProductImage
    if not image_ids:
        return []
    return db.execute(select(Creative.ad_id, Creative.product_id).join(
        ProductImage, (ProductImage.source == "creative") & (ProductImage.source_id == Creative.id))
        .where(ProductImage.id.in_(image_ids))).all()


# ---------------- clusters / ownership ----------------

def _red(db):
    from .recommend import redirects
    return redirects(db)


def _root(red, pid):
    from .recommend import root
    return root(red, pid)


def allocation_of(db, pids, uid):
    from .recommend import lock_owners
    owners = lock_owners(db, set(pids))
    return {p: ("available" if owners.get(p) is None else "mine" if owners[p] == uid else "taken") for p in pids}


# ---------------- similar products ----------------

def _product_text(db, pids):
    """Name + Vietnamese name + up to 5 ad titles / copies per product (multilingual: lexicon maps every language)."""
    names = {i: f"{n or ''} {vi or ''}" for i, n, vi in db.execute(
        select(Product.id, Product.canonical_name, Product.name_vi).where(Product.id.in_(list(pids))))}
    extra, count = {}, {}
    for pid, title, body in db.execute(select(Ad.product_id, Ad.title, Ad.ad_text).where(Ad.product_id.in_(list(pids))).order_by(Ad.id.desc())):
        if count.get(pid, 0) < 5:
            count[pid] = count.get(pid, 0) + 1
            extra[pid] = f"{extra.get(pid, '')} {title or ''} {(body or '')[:300]}"
    return {p: f"{names.get(p, '')} {extra.get(p, '')}" for p in pids}, names


_TEXT = {"at": 0.0, "v": {}}  # product id → (concepts, tokens); text changes slowly, lexicon parsing is the hot path


def text_features(db, pids):
    if time.time() - _TEXT["at"] > 3600:
        _TEXT.update(at=time.time(), v={})
    cache = _TEXT["v"]
    need = [p for p in pids if p not in cache]
    if need:
        texts, _ = _product_text(db, need)
        for p in need:
            cache[p] = (concepts(texts[p]), tokens(texts[p]))
    return {p: cache[p] for p in pids}


def _landings(db, pids, domains=None):
    from .services.cluster_evidence import product_url
    out = {}
    q = select(Ad.product_id, Ad.landing_url).where(Ad.product_id.in_(list(pids)), Ad.landing_url.is_not(None))
    if domains is not None:  # candidates can only share a product URL on the source's own domains
        if not domains:
            return out
        q = q.where(or_(*[Ad.landing_url.like(f"%{d}%") for d in domains]))
    for pid, url in db.execute(q):
        u = product_url(url)
        if u:
            out.setdefault(pid, set()).add(u)
    return out


def _dup_links(db, pid):
    q = select(ProductLink.a_id, ProductLink.b_id).where(
        or_(ProductLink.a_id == pid, ProductLink.b_id == pid), ProductLink.relation_kind == "duplicate_product",
        ProductLink.status.in_(["pending", "accepted", "auto_merged"]))
    return {b if a == pid else a for a, b in db.execute(q)}


def product_candidates(db, src, terms, image_id, red, limit):
    roots_only = Product.id.not_in(list(red)) if red else true()
    base = select(Product.id).where(roots_only, Product.id != src.id, Product.is_demo.is_not(True))
    ids = set()
    if src.category:
        ids |= set(db.scalars(base.where(Product.category == src.category).order_by(Product.opportunity_score.desc()).limit(limit)))
    if terms:
        hit = select(Ad.product_id).where(or_(*[Ad.search_text.like(f"%{t}%") for t in terms])).distinct().limit(limit)
        ids |= set(db.scalars(base.where(Product.id.in_(hit))))
    for _, pid in _creatives_of_images(db, pg_neighbours(db, image_id)):
        if pid:
            ids.add(_root(red, pid))
    ids.discard(src.id)
    return ids


def similar_products(db, pid, uid=None, limit=20, exclusive=False):
    t0 = time.perf_counter()
    red = _red(db)
    src = db.get(Product, _root(red, pid))
    if src is None:
        return None
    members = {p for p in red if _root(red, p) == src.id} | {src.id}  # same cluster = same product, not "similar"
    s_concepts, s_tokens = text_features(db, [src.id])[src.id]
    s_img = _image_rows(db, Creative.product_id, [src.id]).get(src.id)
    cand = product_candidates(db, src, concept_terms(s_concepts, s_tokens), s_img[0] if s_img else None, red,
                              int(env("SIMILAR_PREFILTER", "300"))) - members
    cols = (Product.id, Product.canonical_name, Product.category, Product.subcategory, Product.country, Product.cover_creative_id)
    prods = {p.id: p for p in db.execute(select(*cols).where(Product.id.in_(list(cand))))} if cand else {}
    feats = text_features(db, list(prods))
    imgs = _image_rows(db, Creative.product_id, list(prods))
    lands = _landings(db, [src.id])
    lands.update(_landings(db, list(prods), {u.split("/")[0] for u in lands.get(src.id, ())}))
    linked = {_root(red, x) for x in _dup_links(db, src.id)}
    usable = [k for k, ok in (("image", s_img is not None and s_img[1] is not None), ("concepts", bool(s_concepts)),
                              ("category", bool(src.category)), ("tokens", bool(s_tokens))) if ok]
    denom = sum(PRODUCT_W[k] for k in usable) or 1.0
    similar, dups = [], []
    min_score = env("SIMILAR_MIN", "0.35")
    for cid, p in prods.items():
        c_img = imgs.get(cid)
        c_con, c_tok = feats[cid]
        sig = {"image": cos_score(s_img[1] if s_img else None, c_img[1] if c_img else None),
               "concepts": concept_score(s_concepts, c_con),
               "category": (0.7 if p.category and p.category == src.category else 0.0)
                           + (0.3 if p.subcategory and p.subcategory == src.subcategory and p.category == src.category else 0.0),
               "tokens": jaccard(s_tokens, c_tok)}
        score = sum(PRODUCT_W[k] * sig[k] for k in usable) / denom
        ph = hamming(s_img[2], c_img[2]) if s_img and c_img else 64
        dup_why = ("phase1_duplicate_link" if cid in linked else
                   "same_product_url" if lands.get(src.id, set()) & lands.get(cid, set()) else
                   "same_image_and_name" if ph <= 6 and sig["tokens"] >= 0.5 else None)
        if dup_why is None and score < min_score:
            continue
        reasons = []
        if sig["concepts"] >= 0.8:
            reasons.append("Cùng loại sản phẩm / công dụng: " + ", ".join(sorted(s_concepts & c_con)[:3]))
        if sig["category"] >= 0.7:
            reasons.append(f"Cùng danh mục {src.category}")
        if sig["image"] >= 0.5:
            reasons.append("Hình ảnh tương đồng")
        if sig["tokens"] >= 0.3:
            reasons.append("Tên / nội dung gần giống")
        row = {"product_id": cid, "name": p.canonical_name, "category": p.category, "country": p.country,
               "score": round(100 * score, 1), "signals": {k: round(v, 3) for k, v in sig.items()}, "reasons": reasons,
               "relation": "duplicate_product" if dup_why else "similar_product", "cover_creative_id": p.cover_creative_id}
        if dup_why:
            row["duplicate_evidence"] = dup_why
            dups.append(row)
        else:
            similar.append(row)
    similar.sort(key=lambda r: (-r["score"], r["product_id"]))
    alloc = allocation_of(db, [r["product_id"] for r in similar + dups], uid)
    for r in similar + dups:
        r["allocation"] = alloc[r["product_id"]]
    if exclusive:
        similar = [r for r in similar if r["allocation"] == "available"]
    return {"product_id": src.id, "requested_product_id": pid, "similar": similar[:limit],
            "duplicates": sorted(dups, key=lambda r: -r["score"]), "candidates": len(prods),
            "signals_used": usable, "took_ms": round(1000 * (time.perf_counter() - t0), 1)}


# ---------------- similar ads ----------------

def similar_ads(db, aid, limit=20):
    from .workspace.filters import ad_filter
    t0 = time.perf_counter()
    src = db.get(Ad, aid)
    if src is None:
        return None
    red = _red(db)
    s_text = f"{src.title or ''} {src.ad_text or ''}"
    s_con, s_tok = concepts(s_text), tokens(s_text)
    s_img = _image_rows(db, Creative.ad_id, [aid]).get(aid)
    cap = int(env("SIMILAR_PREFILTER", "300"))
    base = select(Ad.id).where(Ad.id != aid, ad_filter(db))
    ids = set()
    if src.product_id:  # other ads of the same product cluster
        cluster = [p for p in {src.product_id, *red} if _root(red, p) == _root(red, src.product_id)]
        ids |= set(db.scalars(base.where(Ad.product_id.in_(cluster)).limit(100)))
    terms = concept_terms(s_con, s_tok)
    if terms:
        ids |= set(db.scalars(base.where(or_(*[Ad.search_text.like(f"%{t}%") for t in terms])).order_by(Ad.id.desc()).limit(cap)))
    if src.advertiser_id:
        ids |= set(db.scalars(base.where(Ad.advertiser_id == src.advertiser_id).order_by(Ad.id.desc()).limit(50)))
    near = {a for a, _ in _creatives_of_images(db, pg_neighbours(db, s_img[0] if s_img else None)) if a}
    if near:
        ids |= set(db.scalars(base.where(Ad.id.in_(near))))
    ads = {a.id: a for a in db.scalars(select(Ad).where(Ad.id.in_(list(ids))))} if ids else {}
    imgs = _image_rows(db, Creative.ad_id, list(ads))
    dup_pairs = {b if a == aid else a for a, b in db.execute(select(DuplicateAdLink.a_id, DuplicateAdLink.b_id).where(
        or_(DuplicateAdLink.a_id == aid, DuplicateAdLink.b_id == aid)))}
    usable = [k for k, ok in (("image", s_img is not None and s_img[1] is not None), ("text", bool(s_con or s_tok)),
                              ("phash", bool(s_img and s_img[2]))) if ok]
    denom = sum(AD_W[k] for k in usable) or 1.0
    similar, dups = [], []
    for cid, a in ads.items():
        c_img = imgs.get(cid)
        c_text = f"{a.title or ''} {a.ad_text or ''}"
        c_con, c_tok = concepts(c_text), tokens(c_text)
        tok = jaccard(s_tok, c_tok)
        ph = hamming(s_img[2], c_img[2]) if s_img and c_img else 64
        sig = {"image": cos_score(s_img[1] if s_img else None, c_img[1] if c_img else None),
               "text": 0.5 * concept_score(s_con, c_con) + 0.5 * tok,
               "phash": 1.0 if ph <= 6 else 0.5 if ph <= 12 else 0.0}
        score = sum(AD_W[k] * sig[k] for k in usable) / denom
        same_page = bool(src.page_id and src.page_id == a.page_id) or bool(src.advertiser_id and src.advertiser_id == a.advertiser_id)
        dup_why = ("phase1_duplicate_link" if cid in dup_pairs else
                   "same_source_id" if src.external_id and a.external_id == src.external_id and a.source == src.source else
                   "same_creative_and_copy" if ph <= 4 and tok >= 0.9 and same_page else None)
        if dup_why is None and score < env("SIMILAR_MIN", "0.35"):
            continue
        reasons = []
        if sig["image"] >= 0.5 or sig["phash"] >= 0.5:
            reasons.append("Creative / hình ảnh tương đồng")
        if sig["text"] >= 0.4:
            reasons.append("Nội dung quảng cáo gần giống")
        if a.product_id and src.product_id and _root(red, a.product_id) == _root(red, src.product_id):
            reasons.append("Cùng sản phẩm")
        row = {"ad_id": cid, "title": a.title, "advertiser_id": a.advertiser_id, "product_id": a.product_id,
               "language": a.language, "country": a.country, "score": round(100 * score, 1),
               "signals": {k: round(v, 3) for k, v in sig.items()}, "reasons": reasons,
               "relation": "duplicate_ad" if dup_why else "similar_ad"}
        if dup_why:
            row["duplicate_evidence"] = dup_why
            dups.append(row)
        else:
            similar.append(row)
    similar.sort(key=lambda r: (-r["score"], r["ad_id"]))
    return {"ad_id": aid, "similar": similar[:limit], "duplicates": dups, "candidates": len(ads), "signals_used": usable,
            "took_ms": round(1000 * (time.perf_counter() - t0), 1)}


# ---------------- per-MKT opportunity lists ----------------

def for_me(db, uid, limit=30, per_seed=15, max_seeds=10):
    """Similar to what this MKT saved / favourited / tested (winners count, failed tests push away). Exclusive only."""
    from .recommend import context, exclusion, lock_owners
    t0 = time.perf_counter()
    ctx = context(db, uid)
    pos = sorted((p for p, w in ctx["seeds"].items() if w > 0), key=lambda p: -ctx["seeds"][p])[:max_seeds]
    neg = [p for p, w in ctx["seeds"].items() if w < 0][:max_seeds]
    merged = {}
    for seed in pos:
        res = similar_products(db, seed, uid, per_seed)
        for r in (res or {}).get("similar", []):
            best = merged.get(r["product_id"])
            name = ctx["seed_rows"][seed].canonical_name if seed in ctx["seed_rows"] else str(seed)
            if best is None or r["score"] > best["score"]:
                merged[r["product_id"]] = {**r, "because_of": [{"product_id": seed, "name": name}]}
            elif all(b["product_id"] != seed for b in best["because_of"]):
                best["because_of"].append({"product_id": seed, "name": name})
    failed_like = set()
    for seed in neg:
        failed_like |= {r["product_id"] for r in (similar_products(db, seed, uid, 50) or {}).get("similar", []) if r["score"] >= 60}
    owners = lock_owners(db, set(merged))
    prods = {p.id: p for p in db.scalars(select(Product).where(Product.id.in_(list(merged))))} if merged else {}
    rows = [r for pid, r in merged.items() if pid in prods and not exclusion(ctx, prods[pid], owners) and pid not in failed_like]
    rows.sort(key=lambda r: (-r["score"], r["product_id"]))
    return {"seeds": len(pos), "rows": rows[:limit], "total": len(rows), "hidden_like_failed_tests": len(failed_like & set(merged)),
            "took_ms": round(1000 * (time.perf_counter() - t0), 1)}


def from_followed(db, uid, limit=30, per_advertiser=10):
    """Other products of the advertisers this MKT follows that are still open opportunities."""
    from .recommend import context, exclusion, lock_owners
    from .workspace.filters import ad_filter
    t0 = time.perf_counter()
    ctx = context(db, uid)
    if not ctx["followed"]:
        return {"rows": [], "total": 0, "took_ms": round(1000 * (time.perf_counter() - t0), 1)}
    q = (select(Ad.advertiser_id, Ad.product_id, func.count(Ad.id).label("ads"), func.max(Ad.first_seen_at).label("latest"))
         .where(Ad.advertiser_id.in_(ctx["followed"]), Ad.product_id.is_not(None), ad_filter(db))
         .group_by(Ad.advertiser_id, Ad.product_id))
    per, picked = {}, []
    rows = sorted(db.execute(q).all(), key=lambda r: (r.latest or r.ads, r.ads), reverse=True)
    roots = {r.product_id: _root(ctx["red"], r.product_id) for r in rows}
    owners = lock_owners(db, set(roots.values()))
    prods = {p.id: p for p in db.scalars(select(Product).where(Product.id.in_(list(set(roots.values())))))} if rows else {}
    names = dict(db.execute(select(Advertiser.id, Advertiser.name).where(Advertiser.id.in_(ctx["followed"]))).all())
    seen = set()
    for r in rows:
        pid = roots[r.product_id]
        p = prods.get(pid)
        if p is None or pid in seen or exclusion(ctx, p, owners) or per.get(r.advertiser_id, 0) >= per_advertiser:
            continue
        seen.add(pid)
        per[r.advertiser_id] = per.get(r.advertiser_id, 0) + 1
        picked.append({"product_id": pid, "name": p.canonical_name, "category": p.category, "advertiser_id": r.advertiser_id,
                       "advertiser": names.get(r.advertiser_id), "ads": r.ads, "latest_ad_at": r.latest,
                       "cover_creative_id": p.cover_creative_id, "allocation": "available",
                       "reasons": [f"Sản phẩm khác của {names.get(r.advertiser_id) or 'advertiser'} bạn đang Follow"]})
    return {"rows": picked[:limit], "total": len(picked), "took_ms": round(1000 * (time.perf_counter() - t0), 1)}
