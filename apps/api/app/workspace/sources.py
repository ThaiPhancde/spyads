"""Explicit advertiser-query capabilities; absence is not evidence that an ad stopped."""
from dataclasses import dataclass, field, replace
import os
import time

from sqlalchemy import select

from ..collector.base import ConnectorError, FetchParams, NotConfigured
from ..collector.contract import AdRecord
from ..models import Connector


class Unsupported(ConnectorError):
    pass


CAPABILITIES = {
    "meta_library": (True, "Page ID; thư viện công khai. Có thể bị giới hạn hoặc thay schema; không vượt CAPTCHA, không bảo đảm đủ lịch sử."),
    "meta_graph": (True, "Page ID qua API chính thức; cần access token/quyền hợp lệ và quốc gia được API hỗ trợ. Không có media hoặc engagement đầy đủ."),
    "tiktok_ad_library": (True, "Business ID qua thư viện công khai EU/UK; không bao phủ PH/US/VN. API có thể từ chối truy cập hoặc thay đổi."),
    "tiktok_commercial": (False, "Query Ads API hiện không công bố bộ lọc business ID; giữ dữ liệu đã thu thập, chưa hỗ trợ quét riêng advertiser."),
    "tiktok_top_ads": (False, "Creative Center chỉ cung cấp tuyển chọn Top Ads, chưa có truy vấn advertiser ID; không suy ra toàn bộ hoạt động đối thủ."),
    "tiktok_top_ads_headless": (False, "Creative Center thiếu advertiser ID/query được xác minh cho theo dõi tự động."),
    "apify_tiktok_top_ads": (False, "Actor Top Ads chưa hỗ trợ truy vấn advertiser ID được xác minh."),
    "snapchat_ads_library": (False, "Connector chỉ tìm theo tên; không dùng tên để xác nhận danh tính advertiser."),
    "apify_meta": (False, "Nguồn trả phí chưa bật trong tracker; dùng Meta Library hoặc Meta Graph với danh tính riêng."),
}


def capability(advertiser):
    supported, detail = CAPABILITIES.get(advertiser.source, (False, "Nguồn chưa có adapter truy vấn advertiser ID; giữ lịch sử hiện có."))
    if not advertiser.source_advertiser_id or advertiser.source_advertiser_id.startswith("unverified:"):
        return {"supported": False, "detail": "Chưa có ID advertiser do nguồn cung cấp; không gộp hoặc quét theo tên. " + detail}
    return {"supported": supported, "detail": detail}


@dataclass
class ScanResult:
    records: list[AdRecord]
    complete: bool = True
    seen_ids: set[str] = field(default_factory=set)


def fetch(db, advertiser):
    cap = capability(advertiser)
    if not cap["supported"]:
        raise Unsupported(cap["detail"])
    config = db.scalar(select(Connector).where(Connector.adapter == advertiser.source, Connector.enabled.is_(True)).order_by(Connector.id))
    if not config:
        raise NotConfigured("Nguồn đang tắt hoặc chưa được cấu hình")
    config = dict(config.config or {})
    db.commit()  # never hold a DB transaction across source requests
    limit = max(10, min(int(os.getenv("COMPETITOR_MAX_ADS", "200")), 1000))
    max_pages = max(1, min(int(os.getenv("COMPETITOR_MAX_PAGES", "10")), 50))
    pid = advertiser.source_advertiser_id
    records, seen = [], set()
    if advertiser.source == "meta_library":
        from ..collector.adapters.meta import MetaLibraryConnector
        connector = MetaLibraryConnector(config)
        params = FetchParams(page_ids=[pid], active_only=False, limit=limit, extra={"competitor_strict": True})
        state = {}
        for _ in range(max_pages):
            page, state = connector.fetch_page(params, "ALL", state)
            if any(str(r.page_id) != pid for r in page):
                raise ConnectorError("Nguồn trả Ads của advertiser khác; không ghi nhận lượt quét thành công")
            records.extend(page)
            seen.update(state.get("siblings") or [])
            if state.get("done") or len(records) >= limit:
                break
        return ScanResult(records[:limit], bool(state.get("done")) and len(records) <= limit, seen)
    if advertiser.source == "meta_graph":
        from ..collector.adapters.meta import MetaGraphConnector
        connector = MetaGraphConnector(config)
        # An explicit country is required by Graph; never substitute an invented market.
        countries = config.get("countries") or advertiser.country
        if not countries or countries == "ALL":
            raise NotConfigured("Meta Graph cần quốc gia hợp lệ trong config countries hoặc advertiser.country")
        countries = countries.split(",") if isinstance(countries, str) else countries
        connector.rate_limit = replace(connector.rate_limit, retries=0)  # durable scheduler handles retries
        records = list(connector.fetch_ads(FetchParams(page_ids=[pid], countries=countries, active_only=False, limit=limit)))
        if any(str(r.page_id) != pid for r in records):
            raise ConnectorError("Meta Graph trả sai advertiser ID")
        # Connector API does not expose completion evidence, so absence stays unknown.
        return ScanResult(records[:limit], False)
    from ..collector.adapters.ad_libraries import TikTokAdLibraryConnector, map_tiktok_library
    connector = TikTokAdLibraryConnector(config)
    sid = ""
    complete = False
    for page in range(max_pages):
        ts = int(time.time())
        data = connector._call("POST", "/api/v1/search", params={"region": "all", "type": "1", "start_time": ts - 90 * 86400, "end_time": ts},
                               json={"query": "", "query_type": "2", "adv_biz_ids": pid, "order": "last_shown_date,desc",
                                     "offset": page, "search_id": sid, "limit": 12})
        if not isinstance(data.get("data"), list) or "has_more" not in data:
            raise ConnectorError("TikTok không trả schema advertiser hợp lệ")
        for raw in data["data"]:
            detail = connector._call("GET", f"/api/v1/items/{raw['id']}/details", params={"lang": "en"}).get("data") or {}
            rec = map_tiktok_library(raw, detail, [], "")
            if str(rec.page_id) != pid:
                raise ConnectorError("TikTok chưa xác nhận đúng business ID; dữ liệu cũ được giữ lại")
            records.append(rec)
        complete = not data["has_more"]
        sid = data.get("search_id") or sid
        if complete or len(records) >= limit:
            break
    return ScanResult(records[:limit], complete and len(records) <= limit)
