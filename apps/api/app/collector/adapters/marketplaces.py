"""China source layer (docs/spy_ads_product_sourcing_architecture.md §10-§11): where can we import it, at what price?

A supplier listing is not an ad: rows land in channel "commerce" (platforms.py) with price, original price, sold count
and search position, then get matched to the same product as the ads → supplier price = landed-cost anchor for margin.

* AliExpressSearchConnector — product JSON embedded in the public search page (works with plain requests)
1688 / Taobao / Pinduoduo / Alibaba answer a login wall or captcha to plain requests (tested 2026-10-06):
add them through Apify (`apify_actor`, source=1688 …) when needed.
"""
from __future__ import annotations

import json
import re
from typing import Iterator

from ..base import FetchParams, RateLimit
from ..contract import AdRecord, MediaItem
from .free import _CffiConnector


def _short(name: str | None, fallback: str) -> str:
    return " ".join(str(name or "").split(",")[0].split()[:9]) or fallback


_SOLD = re.compile(r"([\d.,]+)\s*([kK])?\+?\s*sold")


def parse_sold(text: str | None) -> int | None:
    m = _SOLD.search(text or "")
    if not m:
        return None
    return int(float(m.group(1).replace(",", "")) * (1000 if m.group(2) else 1))


_ALI_ITEM = re.compile(r'"productId":"(\d+)","lunchTime"')


def parse_aliexpress(page: str, query: str, limit: int = 60) -> Iterator[AdRecord]:
    starts = [m for m in _ALI_ITEM.finditer(page)]
    for pos, m in enumerate(starts[:limit], 1):
        chunk = page[m.start(): starts[pos].start() if pos < len(starts) else m.start() + 6000][:6000]
        title = re.search(r'"displayTitle":"((?:[^"\\]|\\.)*)"', chunk)
        if not title:
            continue
        img = re.search(r'"imgUrl":"([^"]+)"', chunk)
        sale = re.search(r'"salePrice":\{[^}]*?"currencyCode":"(\w+)"[^}]*?"minPrice":([\d.]+)', chunk)
        orig = re.search(r'"originalPrice":\{[^}]*?"minPrice":([\d.]+)', chunk)
        trade = re.search(r'"tradeDesc":"([^"]+)"', chunk)
        launch = re.search(r'"lunchTime":"([\d-]+) ([\d:]+)"', chunk)
        name = json.loads(f'"{title.group(1)}"')
        url = f"https://www.aliexpress.com/item/{m.group(1)}.html"
        src = img.group(1) if img else None
        yield AdRecord(
            source="aliexpress_search", source_ad_id=m.group(1), platform="aliexpress", platforms=["aliexpress"],
            title=name, product_name=_short(name, query), ad_text=name, creative_type="image",
            media=[MediaItem("image", "https:" + src if src.startswith("//") else src)] if src else [],
            landing_page=url, product_url=url, price=float(sale.group(2)) if sale else None,
            currency=sale.group(1) if sale else None, original_price=float(orig.group(1)) if orig else None,
            sold_count=parse_sold(trade.group(1) if trade else None), rank=pos,
            first_seen=f"{launch.group(1)}T{launch.group(2)}" if launch else None, active=True, snapshot_url=url,
            matched_query=query, raw_source={"productId": m.group(1), "title": name, "trade": trade.group(1) if trade else None},
        )


class AliExpressSearchConnector(_CffiConnector):
    key = "aliexpress_search"
    name = "AliExpress (free · giá nhập & số đã bán)"
    kind = "browser"
    group = "marketplace"
    rate_limit = RateLimit(requests_per_minute=8)
    supports_search = True
    config_fields = [{"key": "keywords", "label": "Keyword (mỗi dòng 1) cho lịch tự động"}]

    def fetch_ads(self, params: FetchParams) -> Iterator[AdRecord]:
        q = (params.query or "").strip()
        if not q:
            return
        slug = re.sub(r"[^a-z0-9]+", "-", q.lower()).strip("-")
        page = self._req("GET", f"https://www.aliexpress.com/w/wholesale-{slug}.html",
                         cookies={"aep_usuc_f": "site=glo&c_tp=USD&region=US&b_locale=en_US"}).text  # USD prices, not local
        yield from parse_aliexpress(page, q, params.limit)
