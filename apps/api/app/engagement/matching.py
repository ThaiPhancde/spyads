"""Ad → the public post it was made from, by the project's own identity signals (the same families services/clustering.py uses to
decide two products are one): same page (the pool is that page's posts), same landing page, same copy, same picture (perceptual hash).

Meta Ad Library ids are ad ids, not post ids, so a post is never read off an id: the page's recent posts (Bright Data "Facebook - Pages Posts
by Profile URL", one request per page, cached in page_posts) are the candidates and an ad is attached only when the evidence is strong:
  landing  ad.landing_url == post.external_link (landing_key: shop fronts / social links are not identities)       .85
  copy     ad copy ≈ post content (normalised, ≥ 20 chars; ≥ 0.9 → .85, 0.6–0.9 → .50)
  phash    the ad's cover picture ≈ the post's first picture (≤ PHASH_DIST bits), computed only when the text evidence is not enough  .70
score = 1 − Π(1 − w); accepted at ≥ ACCEPT (two families, or one very strong) and only when no other post is within GAP of it (ambiguous → none).
Numbers then come from the stored post — no second request. No confident match = N/A with the reason, never the nearest post.
CLIP is not used here: it would need the model loaded just to embed post pictures; pHash decides, and clustering's CLIP index is untouched.
"""
from __future__ import annotations

import logging
import os
import re
import unicodedata
from datetime import datetime
from difflib import SequenceMatcher

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Ad, Creative
from ..services.entity_resolution import TRIVIAL_PHASH, landing_key
from ..visual.models import ProductImage
from .models import PagePost

log = logging.getLogger("app.engagement.matching")
ACCEPT = float(os.getenv("ENGAGEMENT_MATCH_ACCEPT", "0.90"))
GAP = 0.15
PHASH_DIST = 6
MAX_IMAGE_CHECKS = 3   # downloads per ad at most: only the top text candidates when text alone is not enough


def _norm(s: str | None) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(ch for ch in s if not unicodedata.combining(ch)).lower()
    return re.sub(r"[^\w]+", " ", s).strip()


def copy_similarity(ad_text: str | None, content: str | None) -> float:
    a, b = _norm(ad_text)[:300], _norm(content)[:300]
    if len(a) < 20 or len(b) < 20:
        return 0.0
    if a[:150] == b[:150] or a in b or b in a:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def _w(c: float) -> float:
    return 0.85 if c >= 0.9 else 0.50 if c >= 0.6 else 0.0


def combine(ws: dict[str, float]) -> float:
    rest = 1.0
    for w in ws.values():
        rest *= 1 - w
    return round(1 - rest, 3)


def post_phash(db: Session, post: PagePost) -> str | None:
    """Perceptual hash of the post's first picture (downloaded once, SSRF-checked fetch of the image search, kept on the row)."""
    if post.phash:
        return post.phash
    from ..visual import service as vs

    for url in (post.image_urls or [])[:2]:
        try:
            h = vs.hashes(vs.open_image(vs.fetch_image(url, max_bytes=2_000_000, timeout=8)))["phash"]
        except Exception as e:  # noqa: BLE001 - one expired CDN link: the next picture / no image evidence
            log.info("post %s picture: %s", post.post_id, str(e)[:80])
            continue
        if h and h not in TRIVIAL_PHASH:
            post.phash = h
            return h
    return None


def ad_phash(db: Session, ad_id: int) -> str | None:
    return db.scalar(select(ProductImage.phash).join(Creative, Creative.id == ProductImage.source_id).where(
        ProductImage.source == "creative", Creative.ad_id == ad_id, ProductImage.phash.is_not(None)).order_by(Creative.position, Creative.id).limit(1))


def best_post(db: Session, ad: Ad, posts: list[PagePost]) -> tuple[PagePost | None, float, list[str], str | None]:
    """(post, score, families, reason). reason is set when nothing is attached: no_posts · no_confident_post_match · ambiguous_post_match."""
    if not posts:
        return None, 0.0, [], "no_posts"
    lk = landing_key(ad.landing_url)
    scored = []
    for p in posts:
        ws, fams = {}, []
        if lk and landing_key(p.external_link) == lk:
            ws["landing"], fams = 0.85, [*fams, "landing"]
        c = copy_similarity(ad.ad_text or ad.title, p.content)
        if _w(c):
            ws["copy"], fams = _w(c), [*fams, "copy"]
        scored.append([p, ws, fams])
    scored.sort(key=lambda t: -combine(t[1]))
    if combine(scored[0][1]) < ACCEPT:   # text evidence is not enough: let the picture speak for the top candidates
        mine = ad_phash(db, ad.id)
        if mine and mine not in TRIVIAL_PHASH:
            for t in scored[:MAX_IMAGE_CHECKS]:
                if not t[1] and t is not scored[0]:
                    continue
                h = post_phash(db, t[0])
                if h and (int(h, 16) ^ int(mine, 16)).bit_count() <= PHASH_DIST:
                    t[1]["phash"], t[2] = 0.70, [*t[2], "phash"]
            scored.sort(key=lambda t: -combine(t[1]))
    top = scored[0]
    s = combine(top[1])
    if s < ACCEPT:
        return None, s, top[2], "no_confident_post_match"
    if len(scored) > 1 and s - combine(scored[1][1]) < GAP:
        return None, s, top[2], "ambiguous_post_match"
    return top[0], s, top[2], None


def store_posts(db: Session, page_key: str, records: list[dict], platform: str = "facebook") -> int:
    """Upsert parsed page-post records (bright data parse_page_post dicts); returns how many posts the page now has stored."""
    now = datetime.utcnow()
    have = {p.post_id: p for p in db.scalars(select(PagePost).where(PagePost.platform == platform, PagePost.page_key == page_key))}
    for r in records:
        if not r.get("post_id"):
            continue
        row = have.get(r["post_id"]) or PagePost(platform=platform, page_key=page_key, post_id=r["post_id"])
        for k in ("url", "content", "external_link", "image_urls", "is_sponsored", "likes", "comments", "shares", "views"):
            setattr(row, k, r.get(k))
        try:
            row.posted_at = datetime.fromisoformat(str(r["posted_at"]).replace("Z", "+00:00")).replace(tzinfo=None) if r.get("posted_at") else None
        except ValueError:
            row.posted_at = None
        row.fetched_at = now
        db.add(row)
        have[r["post_id"]] = row
    db.flush()
    return len(have)
