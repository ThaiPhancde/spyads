"""Win checklist (design §2.4, audit 01 §1.8): the four questions SpyWin answers — fresh video, fresh store page, several
pages really paying, a supplier — as pass / fail / unknown with the number behind each. Built from data already stored
(video identity, store page age, features, listings); no new score. "unknown" whenever the evidence is missing."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from ..models import Ad, Product, Store
from .adsignals import ACTIVE_GRACE_DAYS
from .spy import page_age_flag

VIDEO_NEW_DAYS = 30      # video first seen on any page ≤ this → still fresh
MIN_LIVE_PAGES = 2       # pages with a source-confirmed live ad
SUPPLIER_NETWORKS = {"1688": "1688", "taobao": "Taobao", "aliexpress": "AliExpress"}


def _item(key, label, status, value=None, detail=""):
    return {"key": key, "label": label, "status": status, "value": value, "detail": detail}


def win_checklist(f: dict, video: dict | None, own_video: datetime | None, page_ref: datetime | None, stores,
                  now: datetime | None = None) -> list[dict]:
    """f = product.features · video = primary video facts (videos.primary_for) · own_video = earliest video ad of THIS
    product (fallback when no video identity) · page_ref = date the store page is compared to (same as product_360)."""
    now = now or datetime.utcnow()
    out = []

    vfs = (video or {}).get("video_first_seen")
    if vfs:
        age = (now - vfs).days
        out.append(_item("video_new", "Video còn mới", "pass" if age <= VIDEO_NEW_DAYS else "fail", f"{age} ngày",
                         f"sớm nhất trên {video['pages']} page, tính cả ad đã tắt (ngưỡng ≤ {VIDEO_NEW_DAYS} ngày)"))
    elif own_video:
        # only this product's own ads: the same video may have run earlier elsewhere, so a young age proves nothing
        age = (now - own_video).days
        out.append(_item("video_new", "Video còn mới", "fail" if age > VIDEO_NEW_DAYS else "unknown", f"{age} ngày",
                         "chỉ tính ad video của sản phẩm này (chưa nhận diện được video trên các page khác)"))
    else:
        out.append(_item("video_new", "Video còn mới", "unknown", None, "chưa có ad video"))

    ages = [(s, page_age_flag(s, page_ref)) for s in stores if s.page_published_at]
    if not page_ref or not ages:
        out.append(_item("page_new", "Trang bán mới", "unknown", None, "chưa biết ngày đăng trang bán"))
    else:
        s, a = max(ages, key=lambda x: x[1]["days_older"])
        d = a["days_older"]
        out.append(_item("page_new", "Trang bán mới", "fail" if a["flag"] else "pass",
                         f"cũ hơn video {d} ngày" if d > 0 else f"đăng sau video {-d} ngày",
                         f"{s.domain}: người khác đã bán trước" if a["flag"] else f"{s.domain} · cờ đỏ khi cũ hơn video quá 14 ngày"))

    if not f:
        out.append(_item("many_pages", "Nhiều shop đổ tiền thật", "unknown", None, "sản phẩm chưa được chấm"))
    else:
        live = f.get("advertiser_count", 0) if f.get("active_ads") else 0  # advertiser_count = live pages when any ad is live
        # ponytail: new_advertisers_7d still dates TikTok Top Ads by collection day (features.COLLECTED_DATE_SOURCES)
        new = f.get("new_advertisers_7d", 0)
        out.append(_item("many_pages", "Nhiều shop đổ tiền thật", "pass" if live >= MIN_LIVE_PAGES and new >= 1 else "fail",
                         f"{live} page đang chạy · {new} page mới 7 ngày",
                         f"cần ≥ {MIN_LIVE_PAGES} page có ad được nguồn xác nhận trong {ACTIVE_GRACE_DAYS:g} ngày và ≥ 1 page mới vào"))

    nets = [label for n, label in SUPPLIER_NETWORKS.items() if n in (f.get("marketplaces") or [])]
    price = f.get("supplier_price_usd")
    out.append(_item("supplier", "Có nguồn hàng", "pass" if nets else "fail", f"~${price}" if nets and price else None,
                     " / ".join(nets) if nets else "chưa thấy listing 1688 / Taobao / AliExpress"))
    return out


def checklists(db: Session, products: list[Product], now: datetime | None = None) -> dict[int, list[dict]]:
    """{product_id: checklist} for a page of products: 4 queries whatever the page size (no N+1)."""
    from ..videos import primary_for

    pids = [p.id for p in products]
    if not pids:
        return {}
    prim = primary_for(db, pids)
    first = {pid: (v, a) for pid, v, a in db.execute(
        select(Ad.product_id, func.min(case((Ad.media_type == "video", Ad.first_seen_at))), func.min(Ad.first_seen_at))
        .where(Ad.product_id.in_(pids)).group_by(Ad.product_id))}
    stores = defaultdict(list)
    for s in db.scalars(select(Store).where(Store.product_id.in_(pids), Store.page_published_at.is_not(None))):
        stores[s.product_id].append(s)
    out = {}
    for p in products:
        v, (own_video, own_any) = prim.get(p.id), first.get(p.id, (None, None))
        out[p.id] = win_checklist(p.features or {}, v, own_video, (v or {}).get("video_first_seen") or own_video or own_any,
                                  stores[p.id], now)
    return out
