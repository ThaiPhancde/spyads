"""Product clustering: group confident pairs in an additive overlay, queue uncertain pairs for MKT.

Reuses what the app already computes — no new heavy pipeline:
  landing page   entity_resolution.landing_key (shop fronts, social links, collections are not identities)
  copy           same page + same creative fingerprint / variation key, copy ≥ 20 chars
  picture hash   product_images.phash (perceptual hash, already indexed); a hash shared by > MAX_STOCK_ADS ads is a stock frame
  CLIP           product_images.embedding_vec (pgvector HNSW) or the in-RAM matrix — visual.embed, already indexed
  name           entity_resolution.title_similarity on the products' names
Each evidence family adds a weight; a pair's score is 1 − Π(1 − w): one weak family never reaches AUTO_AT, two agreeing do.

Verdicts (decide(...)):
  auto     score ≥ AUTO_AT and no guard fired → cluster membership, logged as PRODUCT_CLUSTERED
  pending  score ≥ REVIEW_AT, or a *soft* guard fired (names disagree, categories differ, prices 3× apart, merged cluster too
           big) → MKT confirms in /clusters
  blocked  a *hard* guard fired (MKT said "not the same" before, a split / undo separated them, both names MKT-verified and
           different) → never proposed; kept so the report can count the wrong merges that were prevented
A rejected / split / undone pair is a permanent cannot-link. The older heuristics (ingest identity, backfill regroup) stay.
Similar products are recorded separately; original Ads/Products/history are never changed by this pass.

ponytail: one global score, weights are hand-set (see W_*) and calibrated on the 2026-10-14 DB (docs/PRODUCT_CLUSTERING.md); a
learned model needs MKT labels — product_links.status accepted / rejected are exactly those labels.
"""
from __future__ import annotations

import logging
import os
from collections import Counter, defaultdict
from datetime import datetime

import numpy as np
from sqlalchemy import and_, func, select, text
from sqlalchemy.orm import Session

from ..models import Ad, Advertiser, Creative, Event, Product, ProductLink
from ..visual.models import ProductImage
from . import naming as N
from .entity_resolution import TRIVIAL_PHASH, landing_key, merge_products, title_similarity, tokenize
from . import cluster_evidence as CE, cluster_membership as CM
from ..cluster_models import AdClusterMembership, DuplicateAdLink, ProductCluster

ALGORITHM_VERSION = CM.VERSION

log = logging.getLogger(__name__)
AUTO_AT = float(os.getenv("CLUSTER_AUTO_AT", "0.93"))
REVIEW_AT = float(os.getenv("CLUSTER_REVIEW_AT", "0.60"))
CLIP_MIN = float(os.getenv("CLUSTER_CLIP_MIN", "0.90"))
CLIP_K = int(os.getenv("CLUSTER_CLIP_K", "40"))        # neighbours per picture: own product's copies crowd the first ones out
HUB_PRODUCTS = 6          # a picture close to ≥ 6 other products is generic (template, stock, lifestyle): no evidence
MAX_KEY_PRODUCTS = 6      # a page / copy key spread over more products than this is a store front or a template
MAX_STOCK_ADS = 8
MAX_MERGED_ADS = int(os.getenv("CLUSTER_MAX_ADS", "40"))  # auto never builds a cluster bigger than this
UNKNOWN_BUCKET = 8
MAX_AUTO_PER_RUN = int(os.getenv("CLUSTER_MAX_AUTO", "1000"))
W_LANDING, W_COPY, W_PHASH, W_PHASH_SAME_PAGE = 0.85, 0.85, 0.70, 0.90
CONFIRMED = ("VERIFIED", "AUTO_CONFIRMED")


def pair(x: int, y: int) -> tuple[int, int]:
    return (x, y) if x < y else (y, x)


# ------------------------------------------------------------ evidence
class State:
    """Everything one scan needs, loaded with a handful of queries (no per-product round trips)."""

    def __init__(self):
        self.prods: dict[int, dict] = {}
        self.ads: list = []
        self.ad_prod: dict[int, int] = {}
        self.ad_who: dict[int, str] = {}
        self.img_ad: dict[int, int] = {}
        self.cover: dict[int, tuple[int, datetime | None]] = {}   # ad id → (cover image id, embedded_at)
        self.pairs: dict[tuple[int, int], dict] = {}
        self.rejected: set[tuple[int, int]] = set()
        self.duplicate_ads: list[dict] = []


def _sig(st: State, a: int, b: int) -> dict:
    return st.pairs.setdefault(pair(a, b), {"landing": [], "copy": 0, "phash": 0, "clip": 0.0, "clip_n": 0, "name": 0.0, "same_page": False})


def load(db: Session) -> State:
    st = State()
    for r in db.execute(select(Product.id, Product.canonical_name, Product.name_status, Product.category, Product.manual_override,
                               Product.features, Product.product_identity)).all():
        price = (r.features or {}).get("avg_price")
        st.prods[r.id] = {"id": r.id, "name": r.canonical_name, "status": r.name_status or "SUGGESTED", "category": r.category,
                          "manual": bool(r.manual_override), "price": price, "n_ads": 0, "pages": set(),
                          "metadata_items": [r.product_identity or {}], "skus": {}}
    ads = db.execute(select(Ad.id, Ad.product_id, Ad.advertiser_id, Ad.page_id, Ad.landing_url, Ad.creative_fingerprint,
                            Ad.variation_key, Ad.ad_text, Ad.source, Ad.platform, Ad.network, Ad.external_id,
                            Ad.product_metadata).where(Ad.product_id.is_not(None))).all()
    st.ads = ads
    for a in ads:
        who = f"{a.network or a.platform}:{a.page_id}" if a.page_id else (f"adv{a.advertiser_id}" if a.advertiser_id else f"ad{a.id}")
        st.ad_prod[a.id], st.ad_who[a.id] = a.product_id, who
        if p := st.prods.get(a.product_id):
            p["n_ads"] += 1
            p["pages"].add(who)
            p["metadata_items"].append(a.product_metadata or {})
            # SKU is shop-local: a retailer-host + issuer identity (never advertiser display name).
            from urllib.parse import urlsplit
            host = urlsplit(a.landing_url or "").netloc.lower().removeprefix("www.")
            scope = (host, a.source, who) if a.page_id or a.advertiser_id else ((host, a.source) if host else None)
            sku = CE.metadata_values([a.product_metadata or {}]).get("sku", set())
            if scope and sku:
                p["skus"].setdefault(scope, set()).update(sku)
    for p in st.prods.values():
        p["metadata"] = CE.metadata_values(p.pop("metadata_items"))
    st.rejected = {pair(a, b) for a, b in db.execute(select(ProductLink.a_id, ProductLink.b_id).where(ProductLink.status == "rejected"))}
    return st


def load_images(db: Session, st: State) -> dict[int, set[str]]:
    """image → ad map, cover image per ad, picture hashes per ad. Hashes/embeddings were computed by the image index."""
    ph: dict[int, set[str]] = defaultdict(set)
    q = (select(Creative.ad_id, ProductImage.id, ProductImage.phash, ProductImage.embedded_at)
         .join(ProductImage, and_(ProductImage.source == "creative", ProductImage.source_id == Creative.id))
         .where(Creative.ad_id.is_not(None), ProductImage.processing_status == "done").order_by(Creative.ad_id, Creative.position, Creative.id))
    for ad_id, img, h, emb_at in db.execute(q):
        st.img_ad[img] = ad_id
        st.cover.setdefault(ad_id, (img, emb_at))
        if h and h not in TRIVIAL_PHASH:
            ph[ad_id].add(h)
    return ph


def add_key_evidence(st: State, ph: dict[int, set[str]]) -> None:
    """landing page / copy / picture hash shared by ads of different products."""
    keys: dict[tuple[str, str], dict[int, set[str]]] = defaultdict(lambda: defaultdict(set))
    stock: Counter = Counter()
    for a in st.ads:
        who, pid = st.ad_who[a.id], a.product_id
        if lk := CE.product_url(a.landing_url):
            keys["landing", lk][pid].add(who)
        if len((a.ad_text or "").strip()) >= 20:
            if a.creative_fingerprint:
                keys["copy", f"{who}|{a.creative_fingerprint}"][pid].add(who)
            if a.variation_key:
                # Same copy can occur across shops, but a hash alone is not enough to identify an Ad.
                keys["copy", f"v|{a.variation_key}|{CE.normalized_name(a.ad_text)}"][pid].add(who)
        for h in ph.get(a.id, ()):
            keys["phash", h][pid].add(who)
            stock[h] += 1
    for (kind, key), by_prod in keys.items():
        if not 2 <= len(by_prod) <= MAX_KEY_PRODUCTS or (kind == "phash" and stock[key] > MAX_STOCK_ADS):
            continue
        pids = sorted(by_prod)
        for i, x in enumerate(pids):
            for y in pids[i + 1:]:
                s = _sig(st, x, y)
                if by_prod[x] & by_prod[y]:
                    s["same_page"] = True
                if kind == "landing":
                    if key not in s["landing"] and len(s["landing"]) < 3:
                        s["landing"].append(key)
                else:
                    s[kind] += 1


def clip_neighbors(db: Session, ids: list[int], k: int = CLIP_K, floor: float = CLIP_MIN):
    """(image, neighbour image, cosine) for the given images — pgvector HNSW when present, else the in-RAM matrix (visual.embed)."""
    from ..visual import embed

    if not ids:
        return
    if embed.pgvector_ready(db):
        db.execute(text("SET LOCAL hnsw.ef_search = 200"))
        sql = text("SELECT a.id, n.id, n.cos FROM product_images a CROSS JOIN LATERAL ("
                   "SELECT b.id, 1 - (b.embedding_vec <=> a.embedding_vec) AS cos FROM product_images b "
                   "WHERE b.id <> a.id AND b.embedding_vec IS NOT NULL AND b.processing_status = 'done' "
                   "ORDER BY b.embedding_vec <=> a.embedding_vec LIMIT :k) n "
                   "WHERE a.id = ANY(:ids) AND a.embedding_vec IS NOT NULL AND n.cos >= :floor")
        for i in range(0, len(ids), 300):
            for a, b, c in db.execute(sql, {"ids": ids[i:i + 300], "k": k, "floor": floor}):
                yield a, b, float(c)
        return
    embed.INDEX.refresh(db, force=True)
    if not len(embed.INDEX.ids):
        return
    pos = {int(v): i for i, v in enumerate(embed.INDEX.ids)}
    for i in range(0, len(ids), 300):
        chunk = [x for x in ids[i:i + 300] if x in pos]
        if not chunk:
            continue
        sims = embed.INDEX.mat[[pos[x] for x in chunk]] @ embed.INDEX.mat.T
        for row, a in zip(sims, chunk):
            for j in np.argpartition(-row, min(k, len(row) - 1))[:k + 1]:
                b = int(embed.INDEX.ids[j])
                if b != a and row[j] >= floor:
                    yield a, b, float(row[j])


def add_clip_evidence(db: Session, st: State, since: datetime | None = None) -> int:
    """CLIP neighbours of each ad's cover picture. A picture close to many products is generic and says nothing."""
    ids = [img for img, emb_at in st.cover.values() if since is None or (emb_at and emb_at > since)]
    near: dict[int, dict[int, float]] = defaultdict(dict)   # image → {other product: best cosine}
    for a, b, c in clip_neighbors(db, ids):
        pa, pb = st.ad_prod.get(st.img_ad.get(a)), st.ad_prod.get(st.img_ad.get(b))
        if pa and pb and pa != pb and c > near[a].get(pb, 0):
            near[a][pb] = c
    used = 0
    for a, others in near.items():
        if len(others) >= HUB_PRODUCTS:
            continue
        ad_a = st.img_ad[a]
        for pb, c in others.items():
            s = _sig(st, st.ad_prod[ad_a], pb)
            s["clip"], s["clip_n"] = max(s["clip"], round(c, 3)), s["clip_n"] + 1
            used += 1
    return used


def add_name_evidence(st: State) -> None:
    """Identical wording of two named products (≥ 2 words) is evidence on its own; any pair already in play gets its similarity."""
    by_key: dict[frozenset, list[int]] = defaultdict(list)
    for p in st.prods.values():
        if p["status"] != "UNKNOWN" and p["n_ads"]:
            toks = frozenset(CE.normalized_name(p["name"]).split())
            if len(toks) >= 2:
                by_key[toks].append(p["id"])
    for pids in by_key.values():
        if 2 <= len(pids) <= MAX_KEY_PRODUCTS:
            for i, x in enumerate(pids):
                for y in pids[i + 1:]:
                    _sig(st, x, y)
    for (x, y), s in st.pairs.items():
        px, py = st.prods.get(x), st.prods.get(y)
        if px and py and px["status"] != "UNKNOWN" and py["status"] != "UNKNOWN":
            s["name"] = round(title_similarity(px["name"], py["name"]), 3)


# ------------------------------------------------------------ decision
def clip_weight(s: dict) -> float:
    c = s["clip"]
    w = 0.60 if c >= 0.97 else 0.45 if c >= 0.94 else 0.30 if c >= CLIP_MIN else 0.0
    if w and s["clip_n"] >= 3:
        w += 0.10   # several independent picture pairs, not one lucky ad
    if w and s["same_page"]:
        w += 0.10
    return min(w, 0.70)


def name_weight(s: dict, px: dict, py: dict) -> float:
    sim = s["name"]
    both_sure = px["status"] in CONFIRMED and py["status"] in CONFIRMED
    return (0.75 if both_sure else 0.60) if sim >= 0.8 else 0.45 if sim >= 0.6 else 0.0


def score(s: dict, px: dict, py: dict) -> tuple[float, list[str]]:
    ws = {"landing": W_LANDING if s["landing"] else 0.0, "copy": W_COPY if s["copy"] else 0.0,
          "phash": (W_PHASH_SAME_PAGE if s["same_page"] else W_PHASH) if s["phash"] else 0.0,
          "clip": clip_weight(s), "name": name_weight(s, px, py),
          "identity": (0.99 if {"gtin", "duplicate_ad"} & set(s.get("identity", [])) else
                       0.96 if "brand_model" in s.get("identity", []) else
                       0.90 if "scoped_sku" in s.get("identity", []) else 0.0)}
    rest = 1.0
    for w in ws.values():
        rest *= 1 - w
    return round(1 - rest, 3), [k for k, w in ws.items() if w]


def guards(s: dict, px: dict, py: dict, rejected: set) -> tuple[list[str], list[str]]:
    """(hard, soft). Hard = never merge / propose. Soft = a human must look."""
    hard, soft = [], []
    hard.extend(CE.conflicts(px, py))
    if pair(px["id"], py["id"]) in rejected:
        hard.append("cannot_link")
    both_named = px["status"] != "UNKNOWN" and py["status"] != "UNKNOWN"
    low_name = both_named and s["name"] < 0.3
    if low_name and px["manual"] and py["manual"]:
        hard.append("verified_names_differ")
    elif low_name:
        soft.append("name_conflict")
    if px["category"] and py["category"] and px["category"] != py["category"]:
        soft.append("category_conflict")
    if px["price"] and py["price"] and max(px["price"], py["price"]) / min(px["price"], py["price"]) > 3:
        soft.append("price_gap")
    if px["n_ads"] + py["n_ads"] > MAX_MERGED_ADS and not (both_named and s["name"] >= 0.8):
        soft.append("big_cluster")   # same wording on both sides is the same product however big; otherwise look
    if any(p["status"] == "UNKNOWN" and p["n_ads"] >= UNKNOWN_BUCKET for p in (px, py)):
        soft.append("unknown_bucket")  # a big unidentified cluster is often a shop front / mixed bucket: MKT decides
    return hard, soft


def decide(st: State) -> list[dict]:
    out = []
    for (x, y), s in st.pairs.items():
        px, py = st.prods.get(x), st.prods.get(y)
        if not px or not py:
            continue
        sc, fams = score(s, px, py)
        if sc < REVIEW_AT and s["clip"] < CLIP_MIN:
            continue
        hard, soft = guards(s, px, py, st.rejected)
        relation = "duplicate_product" if (s.get("identity") or s["landing"] or s["copy"]
                    or (s["phash"] and s["name"] >= 0.6)) else "similar_product"
        if hard:
            relation = "different_product"
        out.append({"a": x, "b": y, "score": sc, "families": fams, "signals": {**s, "families": fams}, "blockers": hard + soft,
                    "relation_kind": relation, "_hard": bool(hard), "_soft": bool(soft)})
    # ambiguity: a product tied to several partners that are different products (named differently) is a shop front / mixed
    # bucket — every one of those ties needs a human, however strong each looks alone
    partners: dict[int, list[int]] = defaultdict(list)
    for r in out:
        if not r["_hard"]:
            partners[r["a"]].append(r["b"])
            partners[r["b"]].append(r["a"])

    def unrelated(i: int, j: int) -> bool:
        pi, pj = st.prods[i], st.prods[j]
        return pi["status"] != "UNKNOWN" and pj["status"] != "UNKNOWN" and title_similarity(pi["name"], pj["name"]) < 0.3

    for r in out:
        for me, other in ((r["a"], r["b"]), (r["b"], r["a"])):
            if any(o != other and unrelated(other, o) for o in partners[me]):
                if "ambiguous" not in r["blockers"]:
                    r["blockers"].append("ambiguous")
                r["_soft"] = True
    for r in out:
        r["verdict"] = ("blocked" if r.pop("_hard") else "similar" if r["relation_kind"] == "similar_product"
                        else "auto" if r["score"] >= AUTO_AT and not r.get("_soft") else "pending")
        r.pop("_soft", None)
    return sorted(out, key=lambda r: -r["score"])


def choose_target(px: dict, py: dict) -> tuple[dict, dict]:
    """(source, target): the better-named product survives, then MKT-owned, then the bigger, then the older."""
    rank = lambda p: (N.STATUS_RANK.get(p["status"], 1), p["manual"], p["n_ads"], -p["id"])
    return (py, px) if rank(px) > rank(py) else (px, py)


# ------------------------------------------------------------ persistence + merging
def store(db: Session, results: list[dict], complete: bool = True) -> Counter:
    """Upsert candidates. Decided rows (accepted / rejected / auto_merged) are never reopened; pending rows are refreshed."""
    have = {(r.a_id, r.b_id): r for r in db.scalars(select(ProductLink))}
    st = Counter()
    current = {(r["a"], r["b"]) for r in results}
    if complete:
        for key, row in have.items():
            if key not in current and row.status in ("pending", "blocked", "similar"):
                row.status = "stale"
    for r in results:
        key = (r["a"], r["b"])
        row = have.get(key)
        status = r["verdict"] if r["verdict"] in ("blocked", "similar") else "pending"
        if row is None:
            db.add(ProductLink(a_id=key[0], b_id=key[1], score=r["score"], signals=r["signals"], blockers=r["blockers"], status=status,
                               relation_kind=r["relation_kind"], algorithm_version=ALGORITHM_VERSION))
            st[status] += 1
        elif row.status in ("pending", "blocked", "stale", "similar"):
            row.score, row.signals, row.blockers, row.status, row.updated_at = r["score"], r["signals"], r["blockers"], status, datetime.utcnow()
            row.relation_kind, row.algorithm_version = r["relation_kind"], ALGORITHM_VERSION
    db.flush()
    return st


def merge_pair(db: Session, st: State, a: int, b: int, by: str) -> tuple[int, int | None] | None:
    """Group the pair in an overlay; retain both original Products and all Ads/history."""
    px, py = st.prods.get(a), st.prods.get(b)
    if not px or not py:
        return None
    src, tgt = choose_target(px, py)
    s = _sig(st, a, b)
    confidence, _ = score(s, px, py)
    return CM.join(db, src["id"], tgt["id"], confidence, s, by, st)


def auto_merge(db: Session, st: State, results: list[dict], by: str = "auto-cluster") -> tuple[set[int], int]:
    """Group `auto` pairs, strongest first; validate every cross-cluster conflict before union."""
    touched, merged = set(), 0
    for r in results:
        if r["verdict"] != "auto" or merged >= MAX_AUTO_PER_RUN:
            continue
        px, py = st.prods.get(r["a"]), st.prods.get(r["b"])
        if not px or not py or r["a"] in touched or r["b"] in touched:
            continue
        if px["n_ads"] + py["n_ads"] > MAX_MERGED_ADS:
            continue
        row = db.scalar(select(ProductLink).where(ProductLink.a_id == r["a"], ProductLink.b_id == r["b"]))
        if row and row.status in ("rejected", "accepted", "auto_clustered", "auto_merged"):
            continue
        try:
            got = merge_pair(db, st, r["a"], r["b"], by)
        except ValueError as exc:
            if row:
                reasons = str(exc).split(",")
                hard = any(r.startswith("metadata_conflict:") or r in
                           {"cannot_link", "mixed_metadata", "model_number_conflict", "variant_name_conflict", "verified_names_differ"}
                           for r in reasons)
                row.status, row.blockers = "blocked" if hard else "pending", sorted(set((row.blockers or []) + reasons))
            continue
        if not got:
            continue
        tid, ev = got
        if ev is None:
            continue
        if row:
            row.status, row.event_id, row.decided_by, row.decided_at = "auto_clustered", ev, by, datetime.utcnow()
        touched.update((r["a"], r["b"], tid))
        merged += 1
    db.flush()
    return {t for t in touched if t in st.prods}, merged


def mark_stale(db: Session) -> int:
    live = set(db.scalars(select(Product.id)))
    nodes = {c.id: c for c in db.scalars(select(ProductCluster))}
    n = 0
    for row in db.scalars(select(ProductLink).where(ProductLink.status.in_(["pending", "blocked", "similar"]))):
        if row.a_id not in live or row.b_id not in live:
            row.status, n = "stale", n + 1
        elif row.a_id in nodes and row.b_id in nodes and CM.root(nodes, row.a_id) == CM.root(nodes, row.b_id):
            row.status = "clustered"
    return n


def record_cannot_link(db: Session, x: int, y: int, by: str | None) -> None:
    """MKT separated these two (split, undo, "not the same"): never propose them again."""
    a, b = pair(x, y)
    row = db.scalar(select(ProductLink).where(ProductLink.a_id == a, ProductLink.b_id == b))
    now = datetime.utcnow()
    if row:
        row.status, row.decided_by, row.decided_at = "rejected", by, now
    else:
        db.add(ProductLink(a_id=a, b_id=b, status="rejected", decided_by=by, decided_at=now, signals={"manual": True}))


# ------------------------------------------------------------ names
def _ad_input(a, adv: str | None) -> dict:
    return {"channel": a.channel, "title": a.title, "ad_text": a.ad_text, "landing_url": a.landing_url, "advertiser": adv,
            "cta_type": a.cta_type, "cta_text": a.cta_text, "funnel": a.funnel}


def propose_name(ads: list, adv_names: dict) -> dict | None:
    """Name a cluster from what its ads evidence: a name two different pages agree on is AUTO_CONFIRMED; a single shop-page /
    listing name keeps its own precision. Nothing evidenced → None (never guessed)."""
    groups: list[dict] = []
    for a in ads:
        nm = N.best(_ad_input(a, adv_names.get(a.advertiser_id)))
        if not nm["name"]:
            continue
        who = a.page_id or a.advertiser_id or a.id
        g = next((g for g in groups if N.overlap(g["name"], nm["name"]) >= 0.6), None)
        if g is None:
            g = {"name": nm["name"], "source": nm["source"], "conf": nm["confidence"], "pages": set(), "evidence": []}
            groups.append(g)
        g["pages"].add(who)
        if nm["confidence"] > g["conf"]:
            g["name"], g["source"], g["conf"] = nm["name"], nm["source"], nm["confidence"]
        g["evidence"] += [{**e, "ad_id": a.id} for e in nm["evidence"][:1]]
    if not groups:
        return None
    g = max(groups, key=lambda g: (len(g["pages"]), g["conf"]))
    conf = max(g["conf"], N.AUTO_CONFIRM_AT) if len(g["pages"]) >= 2 else g["conf"]
    return {"name": g["name"], "source": g["source"], "confidence": conf, "evidence": g["evidence"][:4],
            "status": "AUTO_CONFIRMED" if conf >= N.AUTO_CONFIRM_AT else "SUGGESTED", "pages": len(g["pages"])}


def suggest_names(db: Session, pids) -> dict:
    """Name suggestions for clusters that changed: evidence vote across the cluster's ads (never touches MKT names). UNKNOWN
    products that still have none get the name of their strongest pending partner as a hint to confirm (not as the name)."""
    out = Counter()
    adv_names = dict(db.execute(select(Advertiser.id, Advertiser.name)).all())
    for pid in pids:
        p = db.get(Product, pid)
        if not p or p.manual_override or p.name_status == "VERIFIED":
            continue
        ads = db.scalars(select(Ad).where(Ad.product_id == pid)).all()
        nm = propose_name(ads, adv_names)
        if nm and N.STATUS_RANK[nm["status"]] > N._rank(p):
            N.absorb(p, nm, nm["evidence"])
            out["named"] += 1
            continue
        if p.name_status == "UNKNOWN":
            for link in db.scalars(select(ProductLink).where((ProductLink.a_id == pid) | (ProductLink.b_id == pid),
                                                              ProductLink.status == "pending").order_by(ProductLink.score.desc())):
                other = db.get(Product, link.b_id if link.a_id == pid else link.a_id)
                if other and other.name_status != "UNKNOWN":
                    N._add_evidence(p, [{"source": "cluster", "value": other.canonical_name, "product_id": other.id, "score": link.score}])
                    out["hinted"] += 1
                    break
    return dict(out)


# ------------------------------------------------------------ mixed clusters already in the data
def detect_impure(db: Session, max_ads: int = 120) -> list[dict]:
    """Products whose ads evidence ≥ 2 *different* names and nothing (page, copy, picture hash, CLIP ≥ 0.88) ties the groups
    together — likely two products merged by mistake. Returns suggested splits; MKT applies them with Tách."""
    from ..visual import embed

    adv_names = dict(db.execute(select(Advertiser.id, Advertiser.name)).all())
    by_prod: dict[int, list] = defaultdict(list)
    for a in db.scalars(select(Ad).where(Ad.product_id.is_not(None))):
        by_prod[a.product_id].append(a)
    cand: dict[int, list[dict]] = {}
    for pid, ads in by_prod.items():
        if not 2 <= len(ads) <= max_ads:
            continue
        groups: list[dict] = []
        for a in ads:
            nm = N.best(_ad_input(a, adv_names.get(a.advertiser_id)))
            if not nm["name"] or nm["source"] not in ("listing", "slug", "adapter", "ad_title"):
                continue
            g = next((g for g in groups if N.overlap(g["name"], nm["name"]) >= 0.34), None)
            if g is None:
                groups.append({"name": nm["name"], "ad_ids": [a.id]})
            else:
                g["ad_ids"].append(a.id)
        if len(groups) >= 2:
            cand[pid] = groups
    if not cand:
        return []
    ad_ids = [i for gs in cand.values() for g in gs for i in g["ad_ids"]]
    rows = {a.id: a for pid in cand for a in by_prod[pid]}
    vec: dict[int, np.ndarray] = {}
    ph: dict[int, set[str]] = defaultdict(set)
    q = (select(Creative.ad_id, ProductImage.embedding, ProductImage.phash).join(ProductImage, and_(ProductImage.source == "creative", ProductImage.source_id == Creative.id))
         .where(Creative.ad_id.in_(ad_ids), ProductImage.processing_status == "done").order_by(Creative.ad_id, Creative.position, Creative.id))
    for ad_id, emb, h in db.execute(q):
        if emb is not None and ad_id not in vec:
            vec[ad_id] = embed.from_bytes(emb)
        if h and h not in TRIVIAL_PHASH:
            ph[ad_id].add(h)
    out = []
    for pid, groups in cand.items():
        def tied(g1, g2) -> bool:
            for i in g1["ad_ids"]:
                for j in g2["ad_ids"]:
                    ai, aj = rows[i], rows[j]
                    if (lk := landing_key(ai.landing_url)) and lk == landing_key(aj.landing_url):
                        return True
                    if ai.page_id and ai.page_id == aj.page_id and ai.creative_fingerprint and ai.creative_fingerprint == aj.creative_fingerprint:
                        return True
                    if ph[i] & ph[j]:
                        return True
                    if i in vec and j in vec and float(vec[i] @ vec[j]) >= 0.88:
                        return True
            return False
        # connected components of name groups through ties; ≥ 2 components = nothing links the two products
        comp = list(range(len(groups)))
        for x in range(len(groups)):
            for y in range(x + 1, len(groups)):
                if tied(groups[x], groups[y]):
                    cx, cy = comp[x], comp[y]
                    comp = [cx if c == cy else c for c in comp]
        if len(set(comp)) >= 2:
            merged = defaultdict(lambda: {"names": [], "ad_ids": []})
            for g, c in zip(groups, comp):
                merged[c]["names"].append(g["name"])
                merged[c]["ad_ids"] += g["ad_ids"]
            out.append({"product_id": pid, "groups": [{"name": m["names"][0], "ads": len(m["ad_ids"]), "ad_ids": m["ad_ids"]} for m in merged.values()]})
    return out


# ------------------------------------------------------------ a whole pass
def scan(db: Session, since: datetime | None = None) -> tuple[State, list[dict]]:
    st = load(db)
    ph = load_images(db, st)
    add_key_evidence(st, ph)
    n_clip = add_clip_evidence(db, st, since)
    add_name_evidence(st)
    CE.add_metadata_evidence(st, _sig)
    ad_identities = defaultdict(list)
    for a in st.ads:
        if a.external_id:
            ad_identities[(a.network or a.platform or a.source, a.external_id)].append(a)
    for identity, ads in ad_identities.items():
        # Star edges describe an equivalence group without an unbounded quadratic expansion.
        ads.sort(key=lambda a: a.id)
        for other in ads[1:]:
            st.duplicate_ads.append({"a_id": ads[0].id, "b_id": other.id,
                                     "evidence": {"network": identity[0], "external_id": identity[1], "kind": "duplicate_ad"}})
            if ads[0].product_id != other.product_id:
                _sig(st, ads[0].product_id, other.product_id).setdefault("identity", []).append("duplicate_ad")
    results = decide(st)
    log.info("clustering: %d products, %d pairs scored, %d clip links", len(st.prods), len(results), n_clip)
    return st, results


def run(db: Session, apply_auto: bool = True, since: datetime | None = None, by: str = "auto-cluster") -> dict:
    """Scan cached evidence, persist review candidates and group confident pairs in the overlay. Caller commits."""
    CM.sync(db)
    st, results = scan(db, since)
    for d in st.duplicate_ads:
        if not db.get(DuplicateAdLink, (d["a_id"], d["b_id"])):
            db.add(DuplicateAdLink(**d))
    stat = {"scored": len(results), **{f"verdict_{k}": v for k, v in Counter(r["verdict"] for r in results).items()}}
    stat["stored"] = dict(store(db, results, complete=since is None))
    touched: set[int] = set()
    if apply_auto:
        touched, stat["merged"] = auto_merge(db, st, results, by)
    stat["stale"] = mark_stale(db)
    stat["duplicate_ads"] = len(st.duplicate_ads)
    stat["product_ids"] = sorted(touched)
    return stat


# ------------------------------------------------------------ undo (shared by /api/products/{id}/merges/{eid}/undo, /api/clusters, the CLI)
def undo_event(db: Session, pid: int, eid: int, by: str | None) -> set[int]:
    """Revert a PRODUCT_MERGED (ads split back out under the old name) or PRODUCT_SPLIT (the split product merged back) event of
    product `pid`. The separated pair becomes a permanent cannot-link: a merge that was undone is not proposed again."""
    from . import entity_resolution as ER

    p, e = db.get(Product, pid), db.get(Event, eid)
    if p and e and e.product_id == pid and e.type == "PRODUCT_CLUSTERED":
        return CM.undo(db, e, by)
    if not p or not e or e.product_id != pid or e.type not in ("PRODUCT_MERGED", "PRODUCT_SPLIT") or (e.data or {}).get("undone"):
        raise ValueError("Không hoàn tác được thao tác này")
    d = dict(e.data or {})
    if e.type == "PRODUCT_MERGED":
        new = ER.split_ads(db, p, d.get("ad_ids") or [], None, by)
        if d.get("source_status") != "UNKNOWN":
            N._rename(new, d["source_name"])
            new.name_vi, new.name_status, new.name_source = d.get("source_name_vi"), d.get("source_status") or "SUGGESTED", "restored"
        touched = {p.id, new.id}
        record_cannot_link(db, p.id, new.id, by)
    else:
        other = db.get(Product, d.get("to_id"))
        if not other:
            raise ValueError("Sản phẩm đã tách không còn")
        merge_products(db, other, p, by=by)
        touched = {p.id}
    e.data = {**d, "undone": True}
    for link in db.scalars(select(ProductLink).where(ProductLink.event_id == eid)):
        link.status, link.decided_by, link.decided_at = "rejected", by, datetime.utcnow()
    return touched


# ------------------------------------------------------------ MKT decisions + numbers
def accept(db: Session, link: ProductLink, by: str | None) -> tuple[int, int | None]:
    """Manual review uses cached evidence and fresh metadata guards; no CLIP/model on requests."""
    CM.sync(db)
    if link.algorithm_version != ALGORITHM_VERSION:
        raise ValueError("Cặp cũ cần được worker đánh giá lại trước khi xác nhận")
    st = load(db)
    px, py = st.prods.get(link.a_id), st.prods.get(link.b_id)
    if not px or not py:
        raise ValueError("Một trong hai sản phẩm không còn")
    src, dst = choose_target(px, py)
    tgt, ev = CM.join(db, src["id"], dst["id"], link.score, link.signals, by, st, manual=True)
    if ev is None:
        raise ValueError("Hai sản phẩm đã cùng một cluster")
    link.status, link.event_id, link.decided_by, link.decided_at = "accepted", ev, by, datetime.utcnow()
    db.flush()
    return tgt, ev


def metrics(db: Session) -> dict:
    """The before / after numbers: how fragmented the catalogue is."""
    q = lambda s: db.execute(text(s)).scalar() or 0
    per = "(select product_id, count(*) n from ads where product_id is not null group by product_id) t"
    out = {
        "products": q("select count(*) from products"),
        "products_with_ads": q(f"select count(*) from {per}"),
        "ads": q("select count(*) from ads where product_id is not null"),
        "single_ad_products": q(f"select count(*) from {per} where n = 1"),
        "unnamed_products": q("select count(*) from products where name_status = 'UNKNOWN'"),
        "products_in_target": q("select count(*) from products where in_target"),
        "unnamed_in_target": q("select count(*) from products where name_status = 'UNKNOWN' and in_target"),
        "single_ad_in_target": q(f"select count(*) from {per} join products p on p.id = t.product_id where t.n = 1 and p.in_target"),
        "pending_links": q("select count(*) from product_links where status = 'pending'"),
    }
    out["single_ad_pct"] = round(100 * out["single_ad_products"] / max(1, out["products_with_ads"]), 1)
    out["single_ad_pct_in_target"] = round(100 * out["single_ad_in_target"] / max(1, out["products_in_target"]), 1)
    out["clusters"] = q("select count(*) from product_clusters where merged_into_id is null")
    out["clustered_ads"] = q("select count(*) from ad_cluster_memberships")
    out["duplicate_ad_links"] = q("select count(*) from duplicate_ad_links")
    return out


PREVENTED = {"name_conflict", "ambiguous", "category_conflict", "price_gap", "verified_names_differ", "cannot_link"}


def prevented(results: list[dict]) -> list[dict]:
    """Strong-looking pairs a guard stopped — the wrong merges the old landing/hash-only rule would have made."""
    return [r for r in results if r["score"] >= AUTO_AT and PREVENTED & set(r["blockers"])]
