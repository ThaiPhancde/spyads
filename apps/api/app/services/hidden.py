"""Hidden Winner is a supplementary evidence verdict, ranked solely by Opportunity v2."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, load_only

from ..markets import ad_scope_filter
from ..models import ADS_ONLY, Ad, Creative, Product, ProductDailySnapshot
from ..visual.models import ProductImage
from .adsignals import days_running
from .product_evidence import advertiser_groups, activity_momentum, creative_replication, external_ads

RULE_VERSION = 3
MAX_ADVERTISERS = 3
TAGS = {"momentum": "📈 Momentum đã xác nhận", "replication": "🎬 Creative nhân bản đã xác nhận",
        "rare": "💎 Chỉ 1 nhà quảng cáo", "proven": "⏳ Có ad chạy ≥7 ngày", "multi_market": "🗺 Nhiều thị trường"}
STATUS_LABELS = {"CONFIRMED": "Hidden Winner đã xác nhận", "WATCH": "Chưa xác nhận / Cần theo dõi",
                 "REJECTED": "Không đạt điều kiện"}


def assess(p, ads, *, snapshots=(), creatives=(), images=None, now=None):
    now = now or datetime.utcnow()
    ads = external_ads(ads)
    active = [a for a in ads if a.is_active]
    advertisers, unknown = advertiser_groups(ads)
    max_days = max((days_running(a, now) for a in active), default=0)
    mom = activity_momentum(snapshots, ads, now)
    replication = creative_replication(active, creatives, images)
    checks = [
        {"key": "advertisers", "passed": 1 <= len(advertisers) <= MAX_ADVERTISERS and not unknown,
         "reason": f"{len(advertisers)} nhà quảng cáo duy nhất trong cụm sản phẩm" + ("; còn quảng cáo thiếu danh tính" if unknown else " (yêu cầu 1–3)")},
        {"key": "longevity", "passed": max_days >= 7, "reason": f"Ad đang chạy lâu nhất: {max_days} ngày (yêu cầu ≥7)"},
        {"key": "opportunity", "passed": bool(p.recommendation) and p.recommendation != "SKIP" and p.category != "non_product",
         "reason": f"Khuyến nghị Opportunity v2: {p.recommendation or 'chưa chấm'}"},
        {"key": "momentum_or_replication", "passed": mom["confirmed"] or replication["confirmed"],
         "reason": " · ".join([mom["reason"], replication["reason"]])},
    ]
    rejected = p.recommendation == "SKIP" or p.category == "non_product" or len(advertisers) > MAX_ADVERTISERS or not active
    status = "CONFIRMED" if all(c["passed"] for c in checks) else "REJECTED" if rejected else "WATCH"
    countries = sorted({c for a in active for c in (a.countries or [a.country]) if c and c != "ALL"})
    tags = (["momentum"] if mom["confirmed"] else []) + (["replication"] if replication["confirmed"] else [])
    if len(advertisers) == 1 and not unknown:
        tags.append("rare")
    if max_days >= 7:
        tags.append("proven")
    if len(countries) >= 3:
        tags.append("multi_market")
    return {"product": p, "rule_version": RULE_VERSION, "status": status, "status_label": STATUS_LABELS[status],
            "tags": tags, "why": [c["reason"] for c in checks], "checks": checks, "momentum": mom,
            "replication": replication, "advertisers": len(advertisers), "active_ads": len(active),
            "max_days": max_days, "countries": countries}


def evaluate(p, ads, f=None, now=None, *, snapshots=(), creatives=(), images=None):
    row = assess(p, ads, snapshots=snapshots, creatives=creatives, images=images, now=now)
    return row if row["status"] == "CONFIRMED" else None


def store_verdict(p, row):
    verdict = {k: v for k, v in row.items() if k != "product"}
    f = dict(p.features or {})
    f.pop("hidden_score", None)
    f["hidden_verdict"] = verdict
    p.features = f
    p.tags = row["tags"] if row["status"] == "CONFIRMED" else []


def compute(db: Session, scope=None, include_unconfirmed=False):
    now = datetime.utcnow()
    by_p, cs, history, ims = defaultdict(list), defaultdict(list), defaultdict(list), {}
    q = select(Ad).options(load_only(Ad.id, Ad.product_id, Ad.external_id, Ad.network, Ad.platform, Ad.channel,
        Ad.is_internal, Ad.is_active, Ad.advertiser_id, Ad.page_id, Ad.first_seen_at, Ad.countries, Ad.country))
    q = q.where(Ad.product_id.is_not(None), ADS_ONLY, Ad.is_internal.is_(False))
    if (flt := ad_scope_filter(scope)) is not None:
        # Scope chooses visible products; advertiser/creative evidence stays on the whole product cluster.
        scoped_products = select(Ad.product_id).where(ADS_ONLY, Ad.is_internal.is_(False), Ad.is_active.is_(True), flt).correlate(None)
        q = q.where(Ad.product_id.in_(scoped_products))
    for a in db.scalars(q):
        by_p[a.product_id].append(a)
    if not by_p:
        return []
    products = list(db.scalars(select(Product).where(Product.id.in_(by_p))))
    eligible = [p.id for p in products if p.recommendation != "SKIP" and p.category != "non_product"]
    active_ids = [a.id for pid in eligible for a in by_p[pid] if a.is_active]
    for c in db.scalars(select(Creative).options(load_only(Creative.id, Creative.ad_id, Creative.product_id, Creative.phash,
                      Creative.sha256, Creative.family_id, Creative.source_url)).where(Creative.product_id.in_(eligible), Creative.ad_id.in_(active_ids))):
        cs[c.product_id].append(c)
    cids = [c.id for lst in cs.values() for c in lst]
    for im in db.scalars(select(ProductImage).options(load_only(ProductImage.source_id, ProductImage.phash, ProductImage.image_sha256,
                         ProductImage.embedding, ProductImage.emb_model)).where(ProductImage.source == "creative", ProductImage.source_id.in_(cids))):
        ims[im.source_id] = im
    for snap in db.scalars(select(ProductDailySnapshot).where(ProductDailySnapshot.product_id.in_(eligible),
                          ProductDailySnapshot.date >= now.date() - timedelta(days=14))):
        history[snap.product_id].append(snap)
    rows = [assess(p, by_p[p.id], snapshots=history[p.id], creatives=cs[p.id], images=ims, now=now) for p in products]
    rows = [r for r in rows if include_unconfirmed or r["status"] == "CONFIRMED"]
    rows.sort(key=lambda r: (-(r["product"].opportunity_score or 0), r["product"].id))
    return rows
