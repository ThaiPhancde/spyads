"""Common Data Contract (spy_app_chat_summary §8).

Every connector — API, export file, browser/actor, webhook — must emit `AdRecord`s.
Fields a source does not provide stay None: never guess or invent data.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

MEDIA_TYPES = ("video", "image")


@dataclass
class MediaItem:
    type: str  # video | image
    url: str  # original URL at the source (fbcdn / tiktokcdn … — often expires within hours)
    preview_url: str | None = None  # poster frame for videos
    width: int | None = None
    height: int | None = None
    duration_sec: float | None = None
    quality: str | None = None  # hd | sd


@dataclass
class AdRecord:
    source: str  # connector/provider that delivered it: meta_library, apify_meta, pipiads, minea, extension …
    source_ad_id: str
    platform: str | None = None  # facebook | instagram | tiktok | messenger | audience_network | snapchat | aliexpress | 1688 | taobao
    platforms: list[str] = field(default_factory=list)  # every placement the ad runs on
    country: str | None = None  # ISO-2
    countries: list[str] = field(default_factory=list)
    page_id: str | None = None
    advertiser: str | None = None  # page / shop name
    advertiser_url: str | None = None
    ad_text: str | None = None
    title: str | None = None
    cta_type: str | None = None  # SHOP_NOW, MESSAGE_PAGE, WHATSAPP_MESSAGE, LEARN_MORE …
    cta_text: str | None = None
    creative_type: str | None = None  # video | image | carousel | dco
    media: list[MediaItem] = field(default_factory=list)
    landing_page: str | None = None
    product_name: str | None = None
    product_url: str | None = None
    price: float | None = None
    currency: str | None = None
    category: str | None = None
    first_seen: str | None = None  # ISO date/datetime
    last_seen: str | None = None
    active: bool | None = None
    variants: int | None = None  # Meta "collation_count": number of ads sharing this creative
    likes: int | None = None
    comments: int | None = None
    shares: int | None = None
    views: int | None = None
    impressions_text: str | None = None  # Ad Library "impressions_with_index" text (EU / political)
    spend_text: str | None = None  # "lower-upper currency"
    reach: int | None = None  # EU total reach / reach estimate
    page_likes: int | None = None
    snapshot_url: str | None = None  # link to the original ad (Ad Library / provider page)
    saved_by: str | None = None  # user who saved it (Chrome extension / manual)
    # commerce listings (China source / competitor stores) — None for ad libraries
    rating: float | None = None
    review_count: int | None = None
    sold_count: int | None = None
    rank: int | None = None  # search position at the source
    original_price: float | None = None
    matched_query: str | None = None  # keyword the source matched this ad for (it may match fields we don't store)
    raw_source: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "AdRecord":
        d = dict(d)
        media = [m if isinstance(m, MediaItem) else MediaItem(**{k: v for k, v in m.items() if k in MediaItem.__dataclass_fields__})
                 for m in (d.pop("media", None) or [])]
        known = {k: v for k, v in d.items() if k in AdRecord.__dataclass_fields__}
        return AdRecord(media=media, **known)


def validate(rec: AdRecord) -> list[str]:
    errs = []
    if not rec.source:
        errs.append("source is required")
    if not rec.source_ad_id:
        errs.append("source_ad_id is required")
    for m in rec.media:
        if m.type not in MEDIA_TYPES:
            errs.append(f"media.type must be one of {MEDIA_TYPES}")
        if not m.url:
            errs.append("media.url is required")
    return errs
