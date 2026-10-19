"""Product name for a price lookup, from the most trustworthy evidence first (spec §IV "nhận dạng tên sản phẩm").

products.canonical_name is often the ad hook, so a text price search needs a better name than that. Sources, best first:
  identity    brand / model / title read from the advertiser's own product page (product_identity, verified)
  evidence    names naming.py grounded in a shop page / marketplace listing / link title (products.name_evidence, ads.raw_product_name)
  metadata    ads.product_metadata title (page JSON-LD / og:title)
  caption     naming.best() over the ads' own caption / headline / landing slug
  ocr         text read off the thumbnail (creatives.ocr_text — creative_enrich.py) that holds a product noun
  clip        the NAMED product whose picture is a near-duplicate (CLIP ≥ 0.93, pgvector / in-RAM index already built for image search)
  lens        what ≥ 2 verified Lens result titles agree on (a search already paid for; no new request)
Each candidate carries its source so the job's step log says where the name came from. No candidate = no text search:
the ladder falls to image / landing rungs and the card says "chưa có tên sản phẩm đáng tin cậy", never a guessed query.
"""
from __future__ import annotations

import re
from collections import Counter

from sqlalchemy import select

from ..services import lexicon
from ..services import naming as N

CLIP_NAME_COS = 0.93
CONFIRMED = ("VERIFIED", "AUTO_CONFIRMED")


def _ok(name: str | None) -> bool:
    return bool(name) and not N.is_bad_product_name(name) and not N.is_bad_name(name)


def _consensus(titles: list[str]) -> str | None:
    """A phrase ≥ 2 titles share (their longest common run of words), at least 2 words — a store-agnostic product name."""
    toks = [re.findall(r"[^\W\d_][\w'-]*", t.lower()) for t in titles]
    best: tuple[int, tuple[str, ...]] = (0, ())
    counts: Counter = Counter()
    for ws in toks:
        seen = set()
        for n in (4, 3, 2):
            for i in range(len(ws) - n + 1):
                g = tuple(ws[i:i + n])
                if g not in seen and not all(w in lexicon.WEAK for w in g):
                    seen.add(g)
                    counts[g] += 1
    for g, c in counts.items():
        if c >= 2 and (len(g), c) > (len(best[1]), best[0]):
            best = (c, g)
    return " ".join(best[1]).title() if best[1] and lexicon.has_product_noun(" ".join(best[1])) else None


def _clip_names(db, p, limit: int = 3) -> list[tuple[str, float]]:
    """Named products that look like this one (near-duplicate picture)."""
    from ..models import Creative, Product
    from ..visual import embed
    from ..visual.models import ProductImage

    own = db.scalar(select(ProductImage).join(Creative, Creative.id == ProductImage.source_id).where(
        ProductImage.source == "creative", Creative.product_id == p.id, ProductImage.embedding.is_not(None)).order_by(ProductImage.id).limit(1))
    if own is None:
        return []
    near = embed.INDEX.topk(db, embed.from_bytes(own.embedding), k=40, min_cos=CLIP_NAME_COS)
    near.pop(own.id, None)
    if not near:
        return []
    rows = db.execute(select(ProductImage.id, Product.id, Product.canonical_name, Product.name_status)
                      .join(Creative, Creative.id == ProductImage.source_id).join(Product, Product.id == Creative.product_id)
                      .where(ProductImage.id.in_(list(near)), ProductImage.source == "creative", Product.id != p.id)).all()
    best: dict[int, tuple[str, float]] = {}
    for img, pid, name, status in rows:
        if status in CONFIRMED and _ok(name) and near[img] > best.get(pid, ("", 0))[1]:
            best[pid] = (name, near[img])
    return sorted(best.values(), key=lambda x: -x[1])[:limit]


def candidates(db, p, ads) -> list[dict]:
    """[{name, source, confidence}] best first, deduplicated (case / punctuation)."""
    from .router import query_text

    out: list[dict] = []

    def add(name, source, conf):
        q = query_text(name)
        if q and _ok(q) and q.lower() not in {c["name"].lower() for c in out}:
            out.append({"name": q, "source": source, "confidence": conf})

    ident = p.product_identity or {}
    add(ident.get("title"), "identity", 0.95)
    if p.name_status in CONFIRMED or (p.name_status == "SUGGESTED" and p.name_source in ("slug", "listing", "adapter")):
        add(p.canonical_name, "evidence", 0.9)
    for e in (p.name_evidence or []):
        if e.get("source") in ("slug", "listing", "adapter"):
            add(e.get("value"), "evidence", 0.85)
    from ..services.entity_resolution import landing_key

    for a in ads:
        # page metadata only from a page that is a product page: an app-store / social / short link (onelink.me, m.me …) shows the crawler
        # whatever it likes — "Yate 1916 Digital Kid's Fashion Sports Watch" had been read off a TikTok install link for a foundation ad
        if landing_key(a.landing_url) and (a.funnel or "") not in ("app", "mess", "form"):
            add((a.product_metadata or {}).get("title"), "metadata", 0.85)
        if a.name_source in ("slug", "listing", "adapter"):
            add(a.raw_product_name, "evidence", 0.85)
    from ..models import Advertiser

    adv = {}
    if ads:
        adv = dict(db.execute(select(Advertiser.id, Advertiser.name).where(Advertiser.id.in_({a.advertiser_id for a in ads if a.advertiser_id}))).all())
    votes: Counter = Counter()
    best_of: dict[str, tuple[str, float]] = {}
    for a in ads[:10]:
        nm = N.best({"channel": a.channel, "title": a.title, "ad_text": a.ad_text, "landing_url": a.landing_url, "advertiser": adv.get(a.advertiser_id),
                     "cta_type": a.cta_type, "cta_text": a.cta_text, "funnel": a.funnel})
        if nm["name"]:
            votes[nm["name"]] += 1
            if nm["confidence"] > best_of.get(nm["name"], ("", 0))[1]:
                best_of[nm["name"]] = (nm["source"], nm["confidence"])
    for name, n in votes.most_common(2):
        src, conf = best_of[name]
        # a shop-page slug / listing title read by naming.best is evidence; copy alone is a caption (twice = more sure)
        add(name, "evidence" if src in ("slug", "listing", "adapter") else "caption", conf if src in ("slug", "listing", "adapter") else (0.8 if n > 1 else 0.65))
    from ..models import Creative

    for text in db.scalars(select(Creative.ocr_text).where(Creative.product_id == p.id, Creative.ocr_text.is_not(None), Creative.ocr_text != "").limit(3)):
        for seg in re.split(r"[\n|·•]+|\s{2,}", text):
            ph = N._phrase(seg, 8, noun=True)
            if ph:
                add(ph, "ocr", 0.6)
    try:
        for name, cos in _clip_names(db, p):
            add(name, "clip", round(min(0.9, cos - 0.05), 2))
    except Exception:  # noqa: BLE001 — the picture index is optional: no CLIP candidate, the other sources stand
        pass
    from ..visual.models import LensSearch

    from ..visual.models import ProductImage

    thumbs = select(ProductImage.image_sha256).join(Creative, Creative.id == ProductImage.source_id).where(
        ProductImage.source == "creative", Creative.product_id == p.id, ProductImage.image_sha256.is_not(None))
    lens = db.scalar(select(LensSearch).where(LensSearch.status == "ok", LensSearch.image_sha256.in_(thumbs)).order_by(LensSearch.id.desc()).limit(1))
    if lens:
        titles = [r.get("title") for r in (lens.results or [])[:10] if r.get("title") and r.get("tier") in ("EXACT", "LIKELY_SAME_PRODUCT")]
        add(_consensus([t for t in titles if t]), "lens", 0.7)
    # a name two independent sources agree on beats a lone, slightly more trusted one
    for c in out:
        c["agreed_by"] = sum(1 for o in out if o is not c and o["source"] != c["source"] and N.overlap(c["name"], o["name"]) >= 0.5)
    return sorted(out, key=lambda c: -(c["confidence"] + 0.1 * c["agreed_by"]))


def best_name(db, p, ads) -> dict | None:
    c = candidates(db, p, ads)
    return c[0] if c else None
