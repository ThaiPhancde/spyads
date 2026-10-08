"""One registry of every network the collector knows (docs/30_commerce_ads_spy_github_sources.md §0).

Three channels, kept apart so listings never inflate ad metrics:
  ads       — ad libraries: the creative is a paid ad (Meta, TikTok, Snapchat)
  commerce  — China source: AliExpress (free), 1688 / Taobao (Apify) — supplier price + sold count
  organic   — viral organic posts (TikTok trending): demand / trend signal, not an ad

Search-engine ads (Google, Bing) and Western retail (Amazon, Walmart, Shopify stores) are out of scope: the app hunts
products sold through social ads that can be imported from China (docs/spy_ads_product_sourcing_architecture.md).

`network` groups placements: facebook + instagram + messenger + audience_network → meta. Storage budgets,
UI filters and scoring all key on the network, so adding a source = one row here + one adapter.
"""
from __future__ import annotations

# network: (label, channel, colour for the UI badge)
NETWORKS: dict[str, tuple[str, str, str]] = {
    "meta": ("Meta", "ads", "#1877F2"),
    "tiktok": ("TikTok", "ads", "#111111"),
    "snapchat": ("Snapchat", "ads", "#E6C800"),
    "pinterest": ("Pinterest", "ads", "#E60023"),
    "aliexpress": ("AliExpress", "commerce", "#E43225"),
    "1688": ("1688", "commerce", "#FF6A00"),
    "taobao": ("Taobao", "commerce", "#FF5000"),
    "tiktok_organic": ("TikTok viral", "organic", "#25F4EE"),
    "other": ("Khác", "ads", "#888888"),
}
CHANNELS = {"ads": "Quảng cáo", "commerce": "Nguồn hàng TQ"}

_PLACEMENT = {"facebook": "meta", "instagram": "meta", "messenger": "meta", "audience_network": "meta", "threads": "meta",
              "whatsapp": "meta"}
# sources whose rows are organic posts even though the platform is an ad network
_ORGANIC_SOURCES = {"tiktok_trending"}


# which network a connector feeds (http / export / apify_actor: decided by their configured `source`)
ADAPTER_NETWORK = {"meta_library": "meta", "meta_graph": "meta", "apify_meta": "meta", "meta_ads": "meta",
                   "tiktok_commercial": "tiktok", "tiktok_ad_library": "tiktok", "tiktok_top_ads": "tiktok",
                   "tiktok_trending": "tiktok_organic", "snapchat_ads_library": "snapchat", "aliexpress_search": "aliexpress",
                   "apify_1688": "1688", "apify_taobao": "taobao", "apify_tiktok_top_ads": "tiktok", "apify_tiktok_ads": "tiktok"}

# sources / networks dropped from the product (2026-10-07): hidden from lists and product evidence; old rows stay in the DB
# TikTok viral videos too (2026-10-07): the team spies TikTok *ads* (Creative Center Top Ads, Ad Library), not organic posts
REMOVED_SOURCES = ("google_ads_transparency", "microsoft_ads_library", "amazon_bestsellers", "walmart_search", "shopify_store",
                   "tiktok_trending")
REMOVED_NETWORKS = ("google", "microsoft", "amazon", "walmart", "shopify", "tiktok_organic")

def connector_network(adapter: str | None, config: dict | None = None) -> str:
    if adapter in ADAPTER_NETWORK:
        return ADAPTER_NETWORK[adapter]
    from .collector.adapters.generic import SOURCE_PLATFORM

    src = str((config or {}).get("source") or "").lower()
    return network_of(SOURCE_PLATFORM.get(src) or src)


def network_of(platform: str | None, source: str | None = None) -> str:
    if source in _ORGANIC_SOURCES:
        return "tiktok_organic"
    p = (platform or "").lower()
    p = _PLACEMENT.get(p, p)
    return p if p in NETWORKS else "other"


def channel_of(network: str) -> str:
    return NETWORKS.get(network, NETWORKS["other"])[1]


def label(network: str) -> str:
    return NETWORKS.get(network, NETWORKS["other"])[0]


def catalogue() -> dict:
    """For the UI: networks grouped by channel, with labels and colours."""
    return {"channels": CHANNELS,
            "networks": [{"key": k, "label": l, "channel": c, "color": col} for k, (l, c, col) in NETWORKS.items() if k != "other" and k not in REMOVED_NETWORKS]}


def backfill(db) -> int:
    """Fill network/channel on rows ingested before these columns existed (cheap: one UPDATE per platform)."""
    from sqlalchemy import update

    from .models import Ad, Creative

    n = 0
    for (platform, source) in db.execute(Ad.__table__.select().with_only_columns(Ad.platform, Ad.source)
                                         .where(Ad.network.is_(None)).distinct()).all():
        net = network_of(platform, source)
        n += db.execute(update(Ad).where(Ad.network.is_(None), Ad.platform.is_not_distinct_from(platform),
                                        Ad.source.is_not_distinct_from(source))
                        .values(network=net, channel=channel_of(net))).rowcount or 0
    for (platform, source) in db.execute(Creative.__table__.select().with_only_columns(Creative.source_platform, Creative.source)
                                         .where(Creative.network.is_(None)).distinct()).all():
        db.execute(update(Creative).where(Creative.network.is_(None), Creative.source_platform.is_not_distinct_from(platform),
                                          Creative.source.is_not_distinct_from(source)).values(network=network_of(platform, source)))
    db.commit()
    return n
