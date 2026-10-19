"""Reuse cluster metadata, source listings and cached visuals before buying a search."""
from sqlalchemy import select, or_, String, cast, func

from ..models import Ad, Creative, Product
from ..services.entity_resolution import find_best_match, title_similarity, TRIVIAL_PHASH
from ..services.product_identity import identity_match, compatible_titles
from ..visual.models import ProductImage


def search_name(p, ads=(), db=None, steps=None):
    """Text-search name of a product. With `db`: the best evidenced candidate (naming_hints.py — identity / evidence / caption / OCR /
    CLIP neighbour / Lens titles) and its source goes into the step log; without: identity + names only (offline callers, tests)."""
    from .router import query_text
    if db is not None:
        from .naming_hints import best_name
        hit = best_name(db, p, list(ads))
        if hit:
            if steps is not None:
                steps.add("name", True, hit["source"], q=hit["name"], confidence=hit["confidence"])
            q = hit["name"]
            identity = p.product_identity or {}
            for key in ("brand", "model", "sku"):
                value = identity.get(key)
                if value and value.casefold() not in q.casefold():
                    q = f"{q} {value}"
            return q[:160]
    identity = p.product_identity or {}
    parts = [identity.get("title"), p.canonical_name]
    parts += [(a.product_metadata or {}).get("title") for a in ads]
    parts += [a.raw_product_name for a in ads if a.name_source not in (None, "none")]
    q = query_text(*parts)
    if not q:
        from ..services.product_identity import valid_gtin
        q = identity.get("gtin") if valid_gtin(identity.get("gtin")) else " ".join(identity[k] for k in ("brand", "model", "sku") if identity.get(k))
        if not q:
            return None
    for key in ("brand", "model", "sku"):
        value = identity.get(key)
        if value and value.casefold() not in q.casefold():
            q = f"{q} {value}"
    return q[:160]


def matched(query, title, identity=None, found=None):
    from .router import overlap
    identity = identity or {}
    verdict = identity_match(identity, found)
    if verdict is False:
        return False
    if verdict is True:
        return True
    return bool(title and compatible_titles(query, title) and (overlap(query, title) >= 2 or title_similarity(query, title) >= .75))


def local_sources(db, p, ads):
    """Only linked listings or a strongly supported metadata/hash/CLIP match; never a look-alike price."""
    linked = [a for a in ads if a.channel == "commerce" and a.price and a.currency]
    if linked:
        return [(a, "product_cluster", 1.0) for a in linked[:5]]
    query = search_name(p, ads, db)
    candidate, score = find_best_match(db, {"name": query or "", "identity": p.product_identity,
                                           "category": p.category, "brand": p.brand})
    ids = {candidate.id} if candidate and candidate.id != p.id and score >= .75 else set()
    if query:
        from .router import tokens
        terms = sorted(tokens(query), key=len, reverse=True)[:3]
        conditions = [Product.canonical_name.ilike(f"%{t}%") for t in terms]
        identity = p.product_identity or {}
        if identity.get("gtin"):
            conditions.append(cast(Product.product_identity, String).contains(identity["gtin"]))
        if conditions:
            # DISTINCT over whole Product rows fails on Postgres (json columns have no equality operator → HTTP 500 / price job error):
            # pick distinct ids first, load the rows after.
            cand_ids = select(Product.id).join(Ad, Ad.product_id == Product.id).where(
                Ad.channel == "commerce", Product.id != p.id, or_(*conditions)).distinct().limit(100)
            candidates = list(db.scalars(select(Product).where(Product.id.in_(cand_ids))))
            candidates.sort(key=lambda x: -title_similarity(query, x.canonical_name))
            ids.update(x.id for x in candidates[:25] if matched(query, x.canonical_name, identity, x.product_identity))
    # Hash identities find named source products even when this cluster still has no name.
    own = list(db.scalars(select(ProductImage).join(Creative, Creative.id == ProductImage.source_id).where(
        ProductImage.source == "creative", Creative.product_id == p.id, ProductImage.phash.is_not(None)).limit(3)))
    for im in own:
        if im.phash in TRIVIAL_PHASH:
            continue
        from ..services.clustering import MAX_STOCK_ADS
        owners = db.scalar(select(func.count(func.distinct(Creative.ad_id))).join(ProductImage, ProductImage.source_id == Creative.id).where(
            ProductImage.source == "creative", ProductImage.phash == im.phash)) or 0
        if owners > MAX_STOCK_ADS:
            continue  # reuse the clustering guard: stock/templates are not product identities
        matched_ids = select(ProductImage.source_id).where(ProductImage.source == "creative", ProductImage.id != im.id,
                        ProductImage.phash == im.phash)
        ids.update(db.scalars(select(Creative.product_id).where(Creative.id.in_(matched_ids), Creative.product_id != p.id).limit(15)))
    if not ids:
        return []
    sources = list(db.scalars(select(Ad).where(Ad.product_id.in_(ids), Ad.channel == "commerce", Ad.price.is_not(None), Ad.currency.is_not(None)).limit(25)))
    out = []
    for a in sources:
        other = db.get(Product, a.product_id)
        verdict = identity_match(p.product_identity, other.product_identity)
        if verdict is False or query and not matched(query, other.canonical_name, p.product_identity, other.product_identity):
            continue
        visual_ok, visual_score = False, 0.0
        images = list(db.scalars(select(ProductImage).join(Creative, Creative.id == ProductImage.source_id).where(
            ProductImage.source == "creative", Creative.product_id == other.id).limit(3)))
        for im in own:
            for jm in images:
                if im.phash and jm.phash and im.phash not in TRIVIAL_PHASH and (int(im.phash, 16) ^ int(jm.phash, 16)).bit_count() <= 6:
                    visual_ok, visual_score = True, 1.0
                if im.embedding and jm.embedding and im.emb_model == jm.emb_model:
                    import numpy as np
                    v, w = np.frombuffer(im.embedding, dtype=np.float16).astype(np.float32), np.frombuffer(jm.embedding, dtype=np.float16).astype(np.float32)
                    if v.shape == w.shape and v.size:
                        cos = float(v @ w) / max(float(np.linalg.norm(v) * np.linalg.norm(w)), 1e-9)
                        if cos >= .95:
                            visual_ok, visual_score = True, max(visual_score, cos)
        if verdict is True or visual_ok:
            out.append((a, "metadata" if verdict is True else "cached_visual", 1.0 if verdict is True else visual_score))
    return out[:5]
