"""Offline check for the free-source parsers. Run: cd apps/api && python test_free_sources.py"""
from app.ingest import classify_funnel


assert classify_funnel(None, "https://www.amazon.ae/dp/B08LHSBG3R") == "other"
assert classify_funnel(None, "https://acme.myshopify.com/products/x") == "ladi"

# --- China source (AliExpress)
from app.collector.adapters.marketplaces import parse_aliexpress, parse_sold

ALI = ('"productId":"111","lunchTime":"2025-08-26 00:00:00","image":{"imgUrl":"//ae-pic.com/a.jpg"},"title":{"displayTitle":"Back Brace \\u0026 Posture"},'
       '"prices":{"originalPrice":{"currencyCode":"USD","minPrice":13.61},"salePrice":{"discount":75,"currencyCode":"USD","minPrice":3.33}},'
       '"trade":{"tradeDesc":"10,000+ sold"}},{"productId":"222","lunchTime":"2024-05-16 00:00:00","title":{"displayTitle":"Second"},'
       '"prices":{"salePrice":{"currencyCode":"USD","minPrice":1.09}},"trade":{"tradeDesc":"4 sold"}}')
ali = list(parse_aliexpress(ALI, "posture corrector"))
assert [a.source_ad_id for a in ali] == ["111", "222"], ali
assert ali[0].title == "Back Brace & Posture" and ali[0].price == 3.33 and ali[0].original_price == 13.61
assert ali[0].sold_count == 10000 and ali[0].media[0].url == "https://ae-pic.com/a.jpg" and ali[0].first_seen == "2025-08-26T00:00:00"
assert ali[1].price == 1.09 and ali[1].sold_count == 4 and ali[1].original_price is None
assert parse_sold("1.2k sold") == 1200 and parse_sold("no sales") is None

# --- one registry: placements → network → channel; storage bucket per network
from app.platforms import channel_of, connector_network, network_of
from app.retention import bucket_of, shares

assert network_of("instagram") == "meta" and network_of("google") == "other" and network_of("bing") == "other"
assert network_of("tiktok", "tiktok_trending") == "tiktok_organic" and channel_of("tiktok_organic") == "organic"
assert channel_of(network_of("aliexpress")) == "commerce" and network_of("myspace") == "other"
assert connector_network("http", {"source": "pipiads"}) == "tiktok" and connector_network("aliexpress_search") == "aliexpress"
assert [bucket_of(n) for n in ("meta", "tiktok_organic", "snapchat", "aliexpress", None)] == ["meta", "tiktok", "ads_other", "commerce", "ads_other"]

# --- only products importable from China as generic goods are discovered
from app.services.enrichment import classify_category

assert classify_category("Hair growth supplement") == classify_category("Nike Air Max") == "non_product"
assert classify_category("Electric Neck Massager") == "health" and classify_category("iPhone 15 Pro case") != "non_product"
assert classify_category("Coffee grinder") != "non_product" and classify_category("Vitamin C serum") == "beauty"
assert abs(sum(shares().values()) - 1) < 1e-9

# --- TikTok Ad Library: list row + detail (landing page, CTA, targeted countries); ms dates; lagged "running" window
import time as _t

from app.collector.adapters.ad_libraries import map_tiktok_library

_ms = lambda days_ago: int((_t.time() - days_ago * 86400) * 1000)
row = {"id": "187", "name": "Cartlynest.Ltd", "first_shown_date": _ms(20), "last_shown_date": _ms(5), "estimated_audience": "1K-10K",
       "videos": [{"video_url": "https://v.tiktokcdn.com/a.mp4", "cover_img": "https://p.tiktokcdn.com/c.jpg"}], "image_urls": [],
       "title": "Posture Corrector"}
det = {"ad": {"external_url": "https://cartlynest.com/products/posture", "call_to_action": "Shop now"},
       "advertiser": {"name": "Cartlynest Ltd", "adv_biz_ids": "766"},
       "targeting": {"location": {"data": [{"region": "gb"}, {"region": "FR"}]}}}
t = map_tiktok_library(row, det, [], "posture corrector")
assert t.source_ad_id == "187" and t.advertiser == "Cartlynest Ltd" and t.page_id == "766" and t.countries == ["FR", "GB"]
assert t.landing_page.endswith("/products/posture") and t.cta_text == "Shop now" and t.active is True
assert t.media[0].type == "video" and t.media[0].preview_url.endswith("c.jpg") and t.first_seen[:10] == _t.strftime("%Y-%m-%d", _t.gmtime(_t.time() - 20 * 86400))
old = map_tiktok_library({**row, "last_shown_date": _ms(30)}, {}, ["DE"], "x")
assert old.active is False and old.countries == ["DE"] and old.country == "DE" and old.landing_page is None

# --- media: links without an expiry (AliExpress / Shopify CDNs) stay playable; signed links expire
from app.routers.intel import source_alive

assert source_alive("https://ae-pic.com/a.jpg") and not source_alive(None)
assert not source_alive("https://video.fbcdn.net/v.mp4?oe=5F000000") and source_alive(f"https://x.fbcdn.net/v.mp4?oe={int(_t.time()) + 9999:X}")
assert not source_alive("https://v.tiktokcdn.com/a.mp4?x-expires=1600000000")

# --- GET requests run read-only (deferred BEGIN): the flag is set only inside the middleware
import asyncio

from app.db import READ_ONLY, ReadOnlyGets

seen = []
async def _app(scope, receive, send):
    seen.append(READ_ONLY.get())
mw = ReadOnlyGets(_app)
asyncio.run(mw({"type": "http", "method": "GET"}, None, None))
asyncio.run(mw({"type": "http", "method": "POST"}, None, None))
assert seen == [True, False] and READ_ONLY.get() is False
print("ok")

# --- TikTok Creative Center Top Ads: list/detail mapping
from app.collector.adapters.ad_libraries import map_tiktok_top_ad

top = map_tiktok_top_ad({"id": "768", "ad_title": "Posture fix", "brand_name": "Acme", "like": 574704, "comment": 9, "ctr": 0.91,
                         "country_code": ["ae", "SA"], "landing_page": "https://acme.com/p",
                         "video_info": {"cover": "https://p16/c.jpg", "duration": 13.7, "video_url": {"1080p": "https://v/1080", "720p": "https://v/720"}}},
                        "US", None)
assert top.source == "tiktok_top_ads" and top.countries == ["AE", "SA", "US"] and top.media[0].url == "https://v/720"
assert top.landing_page == "https://acme.com/p" and top.likes == 574704 and top.raw_source["ctr"] == 0.91
assert map_tiktok_top_ad({"id": "1"}, "SA", "x").media == [] and map_tiktok_top_ad({"id": "1"}, "SA", "x").advertiser is None
from app.platforms import channel_of, network_of
assert channel_of(network_of("1688")) == "commerce" and network_of("shopify") == "other"
print("ok")
