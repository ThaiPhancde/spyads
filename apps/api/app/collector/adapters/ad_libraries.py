"""Official / public ad libraries beyond Meta (docs/30_commerce_ads_spy_github_sources.md §23-§29).

All anonymous. Coverage is what each platform publishes: mostly ads delivered in the EU/EEA (DSA transparency),
so these complement — never replace — Meta Ad Library for ME / US / AU.

* SnapchatAdsLibraryConnector  — adsapi.snapchat.com/v1/ads_library (official, search by paying advertiser name)
* TikTokAdLibraryConnector     — library.tiktok.com/api/v1/search (endpoint used by CheckFirstHQ/Tikadrchivist)
* TikTokTopAdsConnector        — Creative Center Top Ads: top in-feed ads per country, incl. US / SA / AE (not EU-only)
Endpoints/payloads follow ifccod/social-media-research-cli and Tikadrchivist (see reference/tiktoks).
"""
from __future__ import annotations

import logging
import re
import time
from typing import Iterator

from ..base import ConnectorError, FetchParams, NotConfigured, RateLimit
from ..contract import AdRecord, MediaItem
from .free import _CffiConnector, _csv, _iso

log = logging.getLogger(__name__)
EU = {"AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL",
      "PL", "PT", "RO", "SK", "SI", "ES", "SE", "IS", "LI", "NO", "CH", "GB"}


class SnapchatAdsLibraryConnector(_CffiConnector):
    key = "snapchat_ads_library"
    name = "Snapchat Ads Library (free · official, theo tên brand)"
    kind = "official_api"
    group = "transparency"
    rate_limit = RateLimit(requests_per_minute=10)
    supports_search = True
    config_fields = [
        {"key": "keywords", "label": "Tên brand / advertiser (mỗi dòng 1) — Snap không tìm theo từ khoá"},
        {"key": "countries", "label": "Quốc gia EU (FR, DE, NL … — mặc định FR, DE)"},
    ]
    URL = "https://adsapi.snapchat.com/v1/ads_library/ads/search"

    def fetch_ads(self, params: FetchParams) -> Iterator[AdRecord]:
        q = (params.query or "").strip()
        if not q:
            return
        countries = [c.upper() for c in params.countries or [] if c.upper() in EU] or _csv(self.config.get("countries"), "FR,DE")
        body = {"status": "ACTIVE", "paying_advertiser_name": q,
                "countries": ["el" if c.upper() == "GR" else c.lower() for c in countries]}
        cursor, got = "", 0
        while got < params.limit:
            data = self._req("POST", self.URL, params={"cursor": cursor} if cursor else None, json=body).json()
            for w in data.get("ad_previews") or []:
                a = w.get("ad_preview") or {}
                if not a.get("id"):
                    continue
                url = a.get("top_snap_media_download_link")
                mtype = "video" if "video" in str(a.get("top_snap_media_type") or "").lower() else "image"
                land = (a.get("web_view_properties") or {}).get("url")
                got += 1
                yield AdRecord(
                    source=self.key, source_ad_id=str(a["id"]), platform="snapchat", platforms=["snapchat"],
                    countries=sorted({k.upper()[:2] for k in a.get("impressions_map") or {}} or {c.upper() for c in countries}),
                    advertiser=a.get("brand_name") or a.get("paying_advertiser_name") or a.get("profile_name"),
                    title=a.get("headline"), ad_text=a.get("headline") or a.get("name"), cta_text=a.get("call_to_action"),
                    creative_type=mtype, media=[MediaItem(mtype, url)] if url else [], landing_page=land, product_url=land,
                    first_seen=a.get("start_date"), active=True, reach=a.get("impressions_total"), raw_source=a,
                )
            nxt = str((data.get("paging") or {}).get("next_link") or "")
            m = re.search(r"cursor=([^&]+)", nxt)
            cursor = m.group(1) if m else ""
            if not cursor:
                break


class TikTokAdLibraryConnector(_CffiConnector):
    """TikTok Commercial Content Library (library.tiktok.com) — competitor ads delivered in the EU/EEA/UK/CH.

    Reverse-engineered from the site's own JS (docs/TikTok_Ads_Strategy_V3_ToolSpy.md §16-§18, §57), no proxy needed:
      1. GET /ads (session) → GET /api/v1/support-regions → `config_str`, sent back as header X-CCL-STR on every call
         → GET /api/v1/location (server-side session state). Missing any of them = HTTP 421 "system busy".
      2. POST /api/v1/search?region=<all|FR,DE>&type=1&start_time=<unix s>&end_time=<unix s>
         body {query, query_type:"1" (keyword; "" ignores the keyword, "2" = advertiser), order, offset=<page index>,
         search_id, limit:12}. Milliseconds or extra UI filters (ad_type, ages…) → 425/421.
      3. GET /api/v1/items/<id>/details → landing URL, CTA, objective, subject, per-country targeting.
    """

    key = "tiktok_ad_library"
    name = "TikTok Ad Library (free · quảng cáo đối thủ chạy ở EU/UK)"
    kind = "browser"
    group = "transparency"
    rate_limit = RateLimit(requests_per_minute=40)  # ≈1.5 s between calls — faster gets 421
    supports_search = True
    config_fields = [
        {"key": "keywords", "label": "Keyword (mỗi dòng 1) cho lịch tự động"},
        {"key": "countries", "label": "Region (all = mọi nước EU/UK, hoặc FR, DE, IT …)"},
        {"key": "days", "label": "Số ngày gần nhất (mặc định 90)"},
        {"key": "details", "label": "Số ad lấy chi tiết (landing page, CTA, targeting) mỗi lần tìm — mặc định 24"},
        {"key": "proxy", "label": "Proxy (chỉ khi bị chặn GEO_BLOCKED)", "secret": True},
    ]
    BASE = "https://library.tiktok.com"
    PAGE = 12

    def _boot(self):
        from curl_cffi import requests as cr

        self._s = cr.Session(impersonate="chrome", proxy=self.config.get("proxy") or None)
        self._h = {"Referer": f"{self.BASE}/ads", "Origin": self.BASE, "Accept": "application/json"}
        self.bucket.take()
        r = self._s.get(f"{self.BASE}/ads", timeout=30)
        if r.status_code != 429:
            self.bucket.take()
            r = self._s.get(f"{self.BASE}/api/v1/support-regions", headers=self._h, timeout=30)
        if r.status_code == 429:
            del self._h  # not a schema change: back off and bootstrap again (handled in _call)
            raise ConnectorError("RATE_LIMITED: TikTok library trả 429 khi khởi tạo session — chờ rồi thử lại")
        cfg = r.json().get("config_str") if "json" in r.headers.get("content-type", "") else None
        if not cfg:
            raise ConnectorError(f"UPSTREAM_CHANGED: support-regions không trả config_str (HTTP {r.status_code})")
        self._h["X-CCL-STR"] = cfg
        self.bucket.take()
        self._s.get(f"{self.BASE}/api/v1/location", headers=self._h, timeout=30)

    def _call(self, method: str, path: str, **kw) -> dict:
        """One API call with status classification; a 421 re-bootstraps the session once before giving up."""
        for attempt in range(3):
            try:
                if not hasattr(self, "_h"):
                    self._boot()
            except ConnectorError as e:
                if not str(e).startswith("RATE_LIMITED") or attempt == 2:
                    raise
                time.sleep(15 * (attempt + 1))  # 429 at bootstrap: back off, then try the session again
                continue
            self.bucket.take()
            r = self._s.request(method, f"{self.BASE}{path}", headers=self._h, timeout=40, **kw)
            ct = r.headers.get("content-type", "")
            if r.status_code == 200 and "json" in ct:
                return r.json()
            if r.status_code in (421, 500) and attempt < 2:
                del self._h  # session state expired / too fast → new session
                time.sleep(3 * (attempt + 1))
                continue
            if r.status_code == 429 and attempt < 2:
                time.sleep(15 * (attempt + 1))  # rate limited: same session, just slower
                continue
            code = {421: "BOT_BLOCKED", 425: "REQUEST_INVALID", 429: "RATE_LIMITED", 403: "GEO_BLOCKED"}.get(
                r.status_code, "PARSER_BROKEN" if r.status_code == 200 else "UPSTREAM_CHANGED")
            raise ConnectorError(f"{code}: TikTok {path} → HTTP {r.status_code} {ct[:30]} {r.text[:120]!r}")
        raise ConnectorError("BOT_BLOCKED: TikTok trả 421 liên tục")

    def search(self, query: str, region: str, page: int, search_id: str, days: int) -> dict:
        now = int(time.time())
        return self._call("POST", "/api/v1/search",
                          params={"region": region, "type": "1", "start_time": now - days * 86400, "end_time": now},
                          json={"query": query, "query_type": "1", "adv_biz_ids": "", "order": "last_shown_date,desc",
                                "offset": page, "search_id": search_id, "limit": self.PAGE})

    def fetch_ads(self, params: FetchParams) -> Iterator[AdRecord]:
        q = (params.query or "").strip()
        if not q:
            return
        codes = [c.upper() for c in params.countries or [] if c.upper() != "ALL"]
        eu = [c for c in codes if c in EU]
        if codes and not eu:  # PH / SA / US …: the library only holds EU/UK ads — falling back to region "all" mixed GB/US ads into PH jobs
            log.warning("tiktok_ad_library: %s không được thư viện hỗ trợ (chỉ EU/UK) — bỏ qua, 0 kết quả", ",".join(codes))
            return
        region = ",".join(eu) if eu else str(self.config.get("countries") or "all").replace(" ", "")
        days, details_left = int(self.config.get("days") or 90), int(self.config.get("details") or 24)
        page, sid, got, seen = 0, "", 0, set()
        while got < params.limit:
            data = self.search(q, region, page, sid, days)
            rows = [a for a in data.get("data") or [] if a.get("id") and a["id"] not in seen]
            for a in rows:
                seen.add(a["id"])
                det = {}
                if details_left > 0:
                    details_left -= 1
                    try:
                        det = self._call("GET", f"/api/v1/items/{a['id']}/details", params={"lang": "en"}).get("data") or {}
                    except ConnectorError:
                        pass  # the list row alone is still a valid ad
                got += 1
                yield map_tiktok_library(a, det, eu, q)
            sid, page = data.get("search_id") or sid, page + 1
            if not data.get("has_more") or not data.get("data"):
                break

    def health_check(self) -> dict:
        """Broad keywords first (doc §17): one empty niche keyword never means the connector is broken."""
        for kw in ("shop", "fashion", "beauty"):
            if self.search(kw, "FR", 0, "", 30).get("data"):
                return {"status": "healthy", "probe": kw}
        return {"status": "degraded", "error": "SUCCESS_EMPTY: shop / fashion / beauty đều 0 kết quả — nghi API đổi schema"}


class TikTokTopAdsConnector(_CffiConnector):
    """TikTok Creative Center · Top Ads — the best-performing in-feed ads per country (US, EU, SA, AE …), MP4 + likes/CTR.

    Runs on the request headers of the user's own logged-in free Creative Center session (pasted from DevTools);
    the app never generates TikTok signatures itself. /top_ads/v2/list serves ≤ 25 pages × 20 per country / period (500 ads); /top_ads/v2/detail adds landing page,
    every country the ad ran in, comments, shares. Without a keyword the scheduler browses the full top list.
    """

    key = "tiktok_top_ads"
    name = "TikTok Creative Center · Top Ads (free account · US/EU/SA/AE)"
    kind = "browser"
    group = "ad_intel"
    rate_limit = RateLimit(requests_per_minute=40)
    supports_search = True
    browses = True  # scheduled run without a keyword = the whole top list per country
    config_fields = [
        {"key": "headers", "label": "Request headers từ DevTools (cookie, user-sign, timestamp, web-id, anonymous-user-id)",
         "secret": True, "required": True},
        {"key": "countries", "label": "Quốc gia (PH, US, GB, DE, FR, AU, SA, AE …)"},
        {"key": "keywords", "label": "Keyword (mỗi dòng 1) — để trống = toàn bộ top ads"},
        {"key": "period", "label": "Khoảng ngày: 7 / 30 / 180 (mặc định 30)"},
        {"key": "order_by", "label": "Sắp xếp: ctr / for_you / like / impression (mặc định ctr — like toàn ad thương hiệu)"},
        {"key": "objectives", "label": "Mục tiêu giữ lại (mặc định conversion, lead_generation, traffic, product_sales, shop_purchases)"},
        {"key": "pages", "label": "Số trang / quốc gia (20 ad / trang, tối đa 25)"},
        {"key": "details", "label": "Số ad lấy chi tiết (landing page, mọi quốc gia chạy) mỗi quốc gia — mặc định 20"},
    ]
    BASE = "https://ads.tiktok.com/creative_radar_api/v1"

    def _headers(self) -> dict:
        raw = self.config.get("headers")
        if isinstance(raw, dict):
            h = raw
        else:  # "Name: value" lines as copied from DevTools → Request Headers
            h = dict(l.split(":", 1) for l in str(raw or "").splitlines() if ":" in l and not l.startswith(":"))
        h = {k.strip(): v.strip() for k, v in h.items()}
        if not h.get("user-sign") and not h.get("cookie"):
            raise NotConfigured("Dán request headers (cookie, user-sign, timestamp, web-id, anonymous-user-id) từ DevTools "
                                "trên ads.tiktok.com/business/creativecenter khi đã đăng nhập tài khoản free")
        return h

    def _get(self, path: str, **params) -> dict:
        data = self._req("GET", f"{self.BASE}{path}", params=params, headers=self._headers()).json()
        if data.get("code") == 40101:
            raise ConnectorError("AUTH_EXPIRED: Creative Center 'no permission' — dán lại headers mới")
        if data.get("code") not in (0, None):
            raise ConnectorError(f"UPSTREAM_CHANGED: Creative Center {path} → {data.get('code')} {data.get('msg')}")
        return data.get("data") or {}

    def fetch_ads(self, params: FetchParams) -> Iterator[AdRecord]:
        cfg = self.config
        countries = [c.upper() for c in params.countries or [] if c.upper() != "ALL"] or \
            _csv(cfg.get("countries"), "PH,US,GB,DE,FR,AU,SA,AE")
        q = (params.query or "").strip()
        objectives = set(_csv(cfg.get("objectives"), "conversion,lead_generation,traffic,product_sales,shop_purchases"))
        pages = min(25, int(cfg.get("pages") or 25))
        if q:  # a keyword search stops at the requested limit; browsing takes the whole list
            pages = min(pages, -(-params.limit // 20))
        for country in countries:
            details_left = int(cfg.get("details") or 20)
            for page in range(1, pages + 1):
                try:
                    data = self._get("/top_ads/v2/list", period=cfg.get("period") or 30, page=page, limit=20,
                                     order_by=cfg.get("order_by") or "ctr", country_code=country,
                                     **({"keyword": q} if q else {}))
                except ConnectorError:
                    if page == 1:
                        raise
                    break  # past the last page Creative Center answers 40000
                for m in data.get("materials") or []:
                    if str(m.get("objective_key") or "").replace("campaign_objective_", "") not in objectives:
                        continue  # reach / video views = brand awareness, not a product being sold
                    det = {}
                    if details_left > 0:
                        details_left -= 1
                        try:
                            det = self._get("/top_ads/v2/detail", material_id=m["id"])
                        except Exception:
                            pass  # the list row alone is still a valid ad
                    yield map_tiktok_top_ad({**m, **det}, country, q or None)
                if not (data.get("pagination") or {}).get("has_more"):
                    break


def map_tiktok_top_ad(m: dict, country: str, query: str | None) -> AdRecord:
    vid = m.get("video_info") or {}
    urls = vid.get("video_url") or {}
    play = urls.get("720p") or urls.get("1080p") or urls.get("540p") or urls.get("480p") or next(iter(urls.values()), None)
    countries = sorted({c.upper() for c in m.get("country_code") or []} | {country})
    land = m.get("landing_page") or None
    return AdRecord(
        source="tiktok_top_ads", source_ad_id=str(m["id"]), platform="tiktok", platforms=["tiktok"],
        country=country, countries=countries, advertiser=m.get("brand_name") or None,
        title=m.get("ad_title"), ad_text=m.get("ad_title"), creative_type="video" if play else None,
        media=[MediaItem("video", play, preview_url=vid.get("cover"), duration_sec=vid.get("duration"))] if play else [],
        landing_page=land, product_url=land, likes=m.get("like"), comments=m.get("comment"), shares=m.get("share"),
        active=True, matched_query=query, product_name=query,
        snapshot_url=f"https://ads.tiktok.com/business/creativecenter/topads/{m['id']}/pc/en",
        raw_source={k: m.get(k) for k in ("id", "ctr", "cost", "objective_key", "industry_key", "is_search", "source")},
    )


def map_tiktok_library(a: dict, det: dict, regions: list[str], query: str | None) -> AdRecord:
    ad = {**a, **(det.get("ad") or {})}
    adv = det.get("advertiser") or {}
    targeted = sorted({t["region"].upper() for t in ((det.get("targeting") or {}).get("location") or {}).get("data") or []
                       if t.get("region")})
    vids = ad.get("videos") or []
    ms = lambda v: _iso(int(v) // 1000) if v else None  # first/last_shown_date are unix ms
    countries = targeted or regions
    url = ad.get("external_url")
    return AdRecord(
        source="tiktok_ad_library", source_ad_id=str(ad.get("id")), platform="tiktok", platforms=["tiktok"],
        country=countries[0] if len(countries) == 1 else None, countries=countries or ["ALL"],
        advertiser=adv.get("name") or ad.get("name"), page_id=adv.get("adv_biz_ids") or None,
        advertiser_url=((adv.get("tt_user") or {}).get("profile_web_link")),
        title=ad.get("title"), ad_text=ad.get("title"), cta_text=ad.get("call_to_action"),
        creative_type="video" if vids else ("image" if ad.get("image_urls") else None),
        media=[MediaItem("video", v["video_url"], preview_url=v.get("cover_img")) for v in vids if v.get("video_url")]
        + [MediaItem("image", u) for u in ad.get("image_urls") or []],
        landing_page=url, product_url=url, category=None,
        first_seen=ms(ad.get("first_shown_date")), last_seen=ms(ad.get("last_shown_date")),
        # the library publishes last_shown_date with a lag of days: shown in the last 14 days = still running
        active=bool(ad.get("last_shown_date")) and int(ad["last_shown_date"]) / 1000 > time.time() - 14 * 86400,
        impressions_text=ad.get("estimated_audience") or None,
        spend_text=str(ad["spent"]) if ad.get("spent") else None, product_name=query,
        snapshot_url=f"https://library.tiktok.com/ads/detail/?ad_id={ad.get('id')}", raw_source={"ad": a, "detail": det},
    )
