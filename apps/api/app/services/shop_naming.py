"""Shop-based naming fallback (docs/PRODUCT_NAMING.md §10).

1. Provisional label — an UNKNOWN product whose ads come (≥ 80 %) from one clear shop shows "Sản phẩm từ <shop>".
   DISPLAY ONLY: products.shop_name feeds naming.card_fields; canonical_name stays "Chưa xác định sản phẩm", name_status
   stays UNKNOWN, so clustering (entity index skips UNKNOWN), product-name search and Opp Score never see the shop name.
   Brand = page names sharing their first word; no advertiser → dominant landing host. Not for gibberish / promo page names; platforms (Alibaba.com, TikTok Shop) and mostly-service clusters read "Quảng cáo từ <shop>".
2. Inheritance — an UNKNOWN product of a shop that also runs a named product, with a visually verified match (CLIP cosine
   ≥ IMAGE_SAME_COS and pHash within IMAGE_SIMILAR_DIST, or a near-copy pHash ≤ 6) to exactly ONE named product of that
   shop, is merged into it. Matching two named products = ambiguous → left alone: one shop selling many products is never
   collapsed into one. UNKNOWN↔UNKNOWN verified pairs of one shop are grouped (still unnamed).
All SQL, no model call, no image decoding — it reads the stored pHash / CLIP vectors.
"""
from __future__ import annotations

import os
import re
from collections import Counter, defaultdict

from urllib.parse import urlparse

from sqlalchemy import select, text

from ..models import Ad, Advertiser, Product
from . import naming as N

DOMINANT = 0.8
SAME_COS = float(os.getenv("IMAGE_SAME_COS", "0.95"))
SIMILAR_DIST = int(os.getenv("IMAGE_SIMILAR_DIST", "14"))
NEAR_COPY_DIST = 6
MAX_GROUP = 30  # a component of unknown products larger than this is not trusted (chained look-alikes)


def is_platform(name: str | None) -> bool:
    return bool(N.PLATFORMS.match(N._clean(name)))


def clear_shop(name: str | None) -> bool:
    """A page name worth showing: not a CTA / promo line. Platforms pass (labelled "Quảng cáo từ", see refresh)."""
    return bool(name) and (is_platform(name) or not N.is_bad_name(name, None))


GENERIC_FIRST = {"the", "shop", "store", "buy", "official", "online", "best", "new", "mega", "super", "good", "global", "world"}
PROMO = re.compile(r"%\s?off|free\s?ship|sale.*\d|giảm\s?\d", re.I)
NOT_A_SHOP_HOST = re.compile(r"(^|\.)(facebook|fb|instagram|tiktok|whatsapp|wa|messenger|m\.me|t\.me|youtube|google|apple|"
                             r"onelink|adjust|bit\.ly|linktr|beacons|doubleclick|shopee|lazada|temu|amazon|aliexpress|alibaba|"
                             r"1688|taobao|tmall|ebay|walmart|tokopedia|shopify\.com|myshopify|ladipage)", re.I)


def shop_ok(name: str | None) -> bool:
    """A page name worth showing. Looser than a product name: "Mega Deals KSA", "Try Sculpiflex", "kingsport.vn" are shops;
    gibberish ("ff", "zzz", "·") and promo lines are not."""
    n = N._clean(name)
    letters = sum(c.isalpha() for c in n)
    return bool(n) and len(n) >= 3 and letters >= 3 and len(set(n.lower())) > 2 and not PROMO.search(n)


def _brand_key(name: str) -> str:
    """Pages of one brand ("ZION Philippines - Davao", "ZION Philippines - Main", "Metrobank" / "Metrobank Card") share a key:
    the first word when it is a distinctive one, else the whole name."""
    words = re.findall(r"\w+", name.lower())
    return words[0] if words and len(words[0]) >= 4 and words[0] not in GENERIC_FIRST else " ".join(words)


def _brand_label(names: list[str]) -> str:
    """Longest common leading words of the brand's page names; one name → itself."""
    toks = [re.split(r"\s+", n.strip()) for n in names]
    common = []
    for ws in zip(*toks):
        if len({w.lower().strip("-–—") for w in ws}) == 1:
            common.append(ws[0])
        else:
            break
    label = " ".join(common).strip(" -–—·")
    return label if len(label) >= 3 else min(names, key=len)


def _host(url: str | None) -> str | None:
    h = urlparse(url if url and "://" in url else f"https://{url or ''}").netloc.lower().removeprefix("www.")
    return h if "." in h and not NOT_A_SHOP_HOST.search(h) else None


def refresh(db, pids=None) -> int:
    """Recompute products.shop_name / shop_service for UNKNOWN products (all, or just `pids`); returns how many got a label.
    Shop = the dominant (≥ 80 % of ads) brand — page names that share their first word count as one brand — else, for ads
    without an advertiser, the dominant landing-page host (own site, not social / chat / marketplace)."""
    q = (select(Ad.product_id, Advertiser.name, Ad.ad_type, Ad.landing_url).outerjoin(Advertiser, Advertiser.id == Ad.advertiser_id)
         .join(Product, Product.id == Ad.product_id).where(Product.name_status == "UNKNOWN"))
    if pids is not None:
        pids = list(pids)
        if not pids:
            return 0
        q = q.where(Ad.product_id.in_(pids))
    by: dict[int, list] = defaultdict(list)
    for pid, adv, typ, url in db.execute(q):
        by[pid].append((adv.strip() if adv and adv.strip() else None, typ, url))
    shop: dict[int, str | None] = {}
    svc: dict[int, bool] = {}
    for pid, rows in by.items():
        n = len(rows)
        label, is_svc = None, sum(1 for _, t, _ in rows if t in ("SERVICE_AD", "CONTENT_AD")) / n >= 0.5
        brands: dict[str, list[str]] = defaultdict(list)
        for a, _, _ in rows:
            if a and shop_ok(a):
                brands[_brand_key(a)].append(a)
        if brands:
            key, names = max(brands.items(), key=lambda kv: len(kv[1]))
            if len(names) / n >= DOMINANT:
                label = _brand_label(sorted(set(names)))
        if label is None and not any(a for a, _, _ in rows):  # no advertiser at all → the site the ads send to
            hosts = Counter(h for _, _, u in rows if (h := _host(u)))
            if hosts and hosts.most_common(1)[0][1] / n >= DOMINANT:
                label = hosts.most_common(1)[0][0]
        shop[pid] = label
        svc[pid] = is_svc or (label is not None and is_platform(label))  # a marketplace sells everything: "Quảng cáo từ"
    for pid, name in shop.items():
        db.execute(text("UPDATE products SET shop_name = :n, shop_service = :s WHERE id = :p"), {"n": name, "s": svc.get(pid, False), "p": pid})
    if pids is None:  # products that are no longer UNKNOWN (or lost their ads) must not keep a label
        db.execute(text("UPDATE products SET shop_name = NULL WHERE shop_name IS NOT NULL AND name_status IS DISTINCT FROM 'UNKNOWN'"))
    return sum(1 for v in shop.values() if v)


PAIRS_SQL = """
SELECT pu, pn, adv, MAX(1 - (vu <=> vn)) AS cos, MIN(dist) AS dist FROM (
  SELECT au.product_id AS pu, an.product_id AS pn, au.advertiser_id AS adv, iu.embedding_vec AS vu, inn.embedding_vec AS vn,
         bit_count(('x' || iu.phash)::bit(64) # ('x' || inn.phash)::bit(64)) AS dist
  FROM ads au
  JOIN products pu ON pu.id = au.product_id AND pu.name_status = 'UNKNOWN'
  JOIN ads an ON an.advertiser_id = au.advertiser_id AND an.product_id <> au.product_id
  JOIN products pn ON pn.id = an.product_id AND pn.name_status {named}
  JOIN creatives cu ON cu.ad_id = au.id JOIN product_images iu ON iu.source = 'creative' AND iu.source_id = cu.id
  JOIN creatives cn ON cn.ad_id = an.id JOIN product_images inn ON inn.source = 'creative' AND inn.source_id = cn.id
  WHERE au.advertiser_id IS NOT NULL AND iu.phash IS NOT NULL AND inn.phash IS NOT NULL
    AND iu.embedding_vec IS NOT NULL AND inn.embedding_vec IS NOT NULL {extra}
    AND bit_count(('x' || iu.phash)::bit(64) # ('x' || inn.phash)::bit(64)) <= :dist   -- cheap filter first: cosine only on look-alikes
) t GROUP BY 1, 2, 3
HAVING MIN(dist) <= :near OR MAX(1 - (vu <=> vn)) >= :cos
"""


def verified_pairs(db, named: bool) -> list[tuple[int, int, int, float, int]]:
    sql = PAIRS_SQL.format(named="<> 'UNKNOWN'" if named else "= 'UNKNOWN'", extra="" if named else "AND an.product_id > au.product_id")
    return [tuple(r) for r in db.execute(text(sql), {"cos": SAME_COS, "dist": SIMILAR_DIST, "near": NEAR_COPY_DIST})]


GENERIC_TOK = {"shop", "sale", "free", "deal", "deals", "price", "prices", "order", "with", "your", "this", "that", "from", "have",
               "shopee", "temu", "online", "store", "official", "limited", "offer", "shipping", "delivery", "discount", "miss",
               "dont", "more", "only", "today", "best", "quality", "click", "link", "available", "message", "hàng", "giá", "mua"}


def _toks(t: str | None) -> set[str]:
    import re

    return {w for w in re.findall(r"\w{4,}", (t or "").lower()) if w not in GENERIC_TOK and not w.isdigit()}


def text_agrees(db, pairs: list[tuple[int, int]]) -> set[tuple[int, int]]:
    """Second, independent signal besides the picture: the two products' ads share a product page (landing host + path) or
    ≥ 4 distinctive caption words with Jaccard ≥ 0.4. A shop reusing one banner for different products fails this."""
    from .entity_resolution import landing_key

    ids = {x for pr in pairs for x in pr}
    info: dict[int, tuple[set, set]] = {i: (set(), set()) for i in ids}
    for pid, title, text_, url in db.execute(select(Ad.product_id, Ad.title, Ad.ad_text, Ad.landing_url).where(Ad.product_id.in_(list(ids)))):
        info[pid][0].update(_toks(f"{title or ''} {text_ or ''}"))
        if lk := landing_key(url):
            info[pid][1].add(lk)
    def agree(a: int, b: int) -> bool:
        if info[a][1] & info[b][1]:
            return True
        sh, un = info[a][0] & info[b][0], info[a][0] | info[b][0]
        return len(sh) >= 4 and len(sh) / len(un) >= 0.4  # shop boilerplate (phone, "توصيل مجاني") alone must not pass

    return {(a, b) for a, b in pairs if agree(a, b)}


def _clear_advs(db, rows) -> set[int]:
    """Advertiser ids that are a real shop (not Temu / Shopee / TikTok Shop…: marketplaces run banners for many products)."""
    ids = {r[2] for r in rows}
    return {i for i, n in db.execute(select(Advertiser.id, Advertiser.name).where(Advertiser.id.in_(list(ids)))) if clear_shop(n)} if ids else set()


def plan(db) -> dict:
    """What inheritance would do: {inherit: {unknown pid: (named pid, cos, dist)}, ambiguous: n, groups: [[pid…]]}."""
    cand: dict[int, dict[int, tuple]] = defaultdict(dict)
    named_pairs = verified_pairs(db, True)
    clear_n = _clear_advs(db, named_pairs)
    named_pairs = [r for r in named_pairs if r[2] in clear_n]
    ok = text_agrees(db, [(r[0], r[1]) for r in named_pairs])
    for pu, pn, _adv, cos, dist in named_pairs:
        if (pu, pn) in ok:
            cand[pu][pn] = (cos, dist)
    inherit, ambiguous = {}, 0
    for pu, targets in cand.items():
        if len(targets) == 1:
            pn, (cos, dist) = next(iter(targets.items()))
            inherit[pu] = (pn, round(float(cos), 3), int(dist))
        else:
            ambiguous += 1  # matches several named products of the shop: it sells many things — never collapsed
    parent: dict[int, int] = {}

    def find(x):
        while parent.get(x, x) != x:
            parent[x] = parent.get(parent[x], parent[x])
            x = parent[x]
        return x

    unk_pairs = verified_pairs(db, False)
    clear = _clear_advs(db, unk_pairs)
    unk_pairs = [r for r in unk_pairs if r[2] in clear]
    ok_u = text_agrees(db, [(r[0], r[1]) for r in unk_pairs])
    for pu, pn, *_ in unk_pairs:
        if (pu, pn) not in ok_u:
            continue
        if pu in inherit or pn in inherit or pu in cand or pn in cand:  # already settled by a named product
            continue
        a, b = find(pu), find(pn)
        if a != b:
            parent[max(a, b)] = min(a, b)
    comps: dict[int, list[int]] = defaultdict(list)
    for x in list(parent) + list(set(parent.values())):
        comps[find(x)].append(x)
    groups = [sorted(set(g)) for g in comps.values() if 1 < len(set(g)) <= MAX_GROUP]
    return {"inherit": inherit, "ambiguous": ambiguous, "groups": groups}


def apply(db, p: dict) -> dict:
    from .entity_resolution import merge_products

    merged = grouped = 0
    touched: set[int] = set()
    for pu, (pn, cos, dist) in p["inherit"].items():
        src, tgt = db.get(Product, pu), db.get(Product, pn)
        if not src or not tgt or src.name_status != "UNKNOWN" or tgt.name_status == "UNKNOWN":
            continue
        ev = [{"source": "shop_visual", "value": f"{tgt.canonical_name} (cos {cos}, pHash {dist})", "ad_id": None}]
        N._add_evidence(tgt, ev)
        merge_products(db, src, tgt, by="shop_verify")
        touched.add(pn)
        merged += 1
    for g in p["groups"]:
        keep = db.get(Product, g[0])
        if not keep or keep.name_status != "UNKNOWN":
            continue
        for pid in g[1:]:
            src = db.get(Product, pid)
            if src and src.name_status == "UNKNOWN":
                merge_products(db, src, keep, by="shop_verify")
                grouped += 1
        touched.add(g[0])
    db.flush()
    return {"merged_into_named": merged, "unknown_grouped": grouped, "touched": sorted(touched)}
