"""Apify actors with a typed mapping each (the generic `apify_actor` connector guesses fields; these don't).

Token: connector config `token`, else APIFY_TOKEN from .env. Billed per item, so spend is capped three ways:
`max_items` per search (≈ $0.30 for all three enabled actors), APIFY_MAX_RUN_USD per run (Apify stops the actor) and
APIFY_MONTHLY_BUDGET_USD per month (runs refused once this month's usage reaches it).

* 1688      zen-studio/1688-wholesale-scraper        factory price (CNY), sold count, supplier
* Taobao    zen-studio/taobao-search-scraper         retail price (CNY), sales signal, shop
            (sian.agency/taobao-tmall-product-scraper refuses API runs on the free Apify plan)
* TikTok    lexis-solutions/tiktok-top-ads-scraper   Creative Center Top Ads: real ads incl. PH / SEA / ME, CTR + likes
* TikTok    lexis-solutions/tiktok-ads-scraper       TikTok Ad Library: EU/UK only (same data as the free tiktok_ad_library)
"""
from __future__ import annotations

import os
import re
import time
from datetime import datetime, timezone
from typing import Iterator

import httpx

from ..base import BaseConnector, ConnectorError, FetchParams, NotConfigured, RateLimit
from ..contract import AdRecord, MediaItem


def _num(text) -> int | None:
    """'已售800+件' → 800, '1.2万+' → 12000, '100+' → 100."""
    m = re.search(r"([\d.]+)\s*(万|w|k)?", str(text or ""), re.I)
    if not m:
        return None
    mult = {"万": 10000, "w": 10000, "k": 1000}.get((m.group(2) or "").lower(), 1)
    return int(float(m.group(1)) * mult)


def _f(v) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _ms(v) -> str | None:
    return datetime.fromtimestamp(v / 1000, tz=timezone.utc).date().isoformat() if isinstance(v, (int, float)) and v else None


def _pic(u: str | None) -> str | None:
    """Taobao search hosts (g.search*.alicdn.com) serve a cert for another name: the same path works on img.alicdn.com.
    'imgextra/0' style stubs are not images."""
    if not u or len(u.rsplit("/", 1)[-1]) < 3:
        return None
    u = "https:" + u if u.startswith("//") else u
    return re.sub(r"^https?://g\.search\d*\.alicdn\.com", "https://img.alicdn.com", u)


def _short(name: str | None) -> str | None:
    return " ".join(str(name or "").split()[:9]) or None


def _redact(msg: str) -> str:
    """httpx quotes the full URL in its errors — never let `?token=…` reach search_jobs.error / the API."""
    return re.sub(r"token=[^&\s'\"]+", "token=***", msg)


class _Apify(BaseConnector):
    kind = "commercial_api"
    rate_limit = RateLimit(requests_per_minute=30, retries=1)
    supports_search = True
    actor = ""
    max_items = 30
    config_fields = [
        {"key": "token", "label": "APIFY_TOKEN (để trống = lấy từ .env)", "secret": True},
        {"key": "actor", "label": "Actor id (để trống = mặc định)"},
        {"key": "max_items", "label": "Số item tối đa / lần tìm (mỗi item tính phí Apify)"},
    ]

    def cap(self, params: FetchParams) -> int:
        return max(1, min(params.limit, int(self.config.get("max_items") or self.max_items)))

    def check_budget(self, token: str) -> None:
        """Three guards, fail closed: the account's monthly total, this connector's share (TikTok can't starve
        1688 / Taobao and vice versa), and a daily cap so one bad day can't eat the month."""
        used, budget = monthly_usage(token), float(os.getenv("APIFY_MONTHLY_BUDGET_USD") or 14)
        if used is None:  # no usage figure → no paid run
            raise ConnectorError("Apify budget unknown — không đọc được /users/me/limits, không chạy actor trả phí")
        if used >= budget:
            raise ConnectorError(f"Apify đã dùng ${used:.2f} / ngân sách ${budget:.0f} tháng này — tạm dừng để không vượt chi phí "
                                 "(đổi APIFY_MONTHLY_BUDGET_USD trong .env)")
        spend = run_spend(token)
        if spend is None:
            raise ConnectorError("Apify: không đọc được lịch sử run — không chạy actor trả phí")
        actor = (self.config.get("actor") or self.actor).replace("/", "~")
        grp = GROUP_OF.get(actor.replace("~", "/"), "other")
        share = SHARES.get(grp, 0) / 100 * budget
        if spend["month"].get(grp, 0) >= share:
            raise ConnectorError(f"Apify quỹ '{grp}' đã dùng ${spend['month'].get(grp, 0):.2f} / ${share:.2f} tháng này "
                                 f"({SHARES.get(grp, 0)}% của ${budget:.0f}) — chỉnh APIFY_SHARES trong .env nếu cần")
        daily = float(os.getenv("APIFY_DAILY_BUDGET_USD") or budget / 10)
        if spend["day"] >= daily:
            raise ConnectorError(f"Apify đã dùng ${spend['day']:.2f} / trần ngày ${daily:.2f} (APIFY_DAILY_BUDGET_USD) — chờ 0h UTC")

    def run(self, inp: dict) -> list[dict]:
        token = self.config.get("token") or os.getenv("APIFY_TOKEN")
        if not token:
            raise NotConfigured("Thiếu APIFY_TOKEN (.env hoặc cấu hình connector)")
        self.check_budget(token)
        actor = (self.config.get("actor") or self.actor).replace("/", "~")
        try:
            r = self.request("POST", f"https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items",
                             params={"token": token, "timeout": 280,
                                     # hard stop billed by Apify itself: a run never costs more than this
                                     "maxTotalChargeUsd": os.getenv("APIFY_MAX_RUN_USD") or "0.25"},
                             json=inp, timeout=300)
        except Exception as e:  # keep the subclass (AuthExpired…) but strip the token from the text
            raise (type(e) if isinstance(e, ConnectorError) else ConnectorError)(_redact(f"{e}")) from None
        finally:
            _usage["at"] = _spend["at"] = 0  # spent something (even on error the actor may have billed): re-read the counter next time
        items = r.json()
        return items if isinstance(items, list) else []


_usage = {"at": 0.0, "usd": None}
# connector actors → budget group; unknown actors fall in "other" (APIFY_SHARES default gives it a small share)
GROUP_OF = {"zen-studio/1688-wholesale-scraper": "sourcing", "zen-studio/taobao-search-scraper": "sourcing",
            "sian.agency/taobao-tmall-product-scraper": "sourcing",
            "lexis-solutions/tiktok-top-ads-scraper": "tiktok", "lexis-solutions/tiktok-ads-scraper": "tiktok"}
SHARES = {k: float(v) for k, v in (p.split(":") for p in (os.getenv("APIFY_SHARES") or "sourcing:45,tiktok:45,other:10").split(","))}
_spend: dict = {"at": 0.0, "v": None}
_act_names: dict[str, str] = {}


def run_spend(token: str) -> dict | None:
    """{"month": {group: usd}, "day": usd} from the run history (usageTotalUsd per run), cached 60 s; None when unreadable."""
    if time.time() - _spend["at"] < 60:
        return _spend["v"]
    try:
        now = datetime.now(timezone.utc)
        items = httpx.get("https://api.apify.com/v2/actor-runs", params={"token": token, "limit": 1000, "desc": 1},
                          timeout=20).json()["data"]["items"]
        month, day = {}, 0.0
        for it in items:
            if it["startedAt"][:7] != now.strftime("%Y-%m"):
                continue
            aid = it["actId"]
            if aid not in _act_names:
                a = httpx.get(f"https://api.apify.com/v2/acts/{aid}", params={"token": token}, timeout=15).json()["data"]
                _act_names[aid] = f"{a['username']}/{a['name']}"
            g, usd = GROUP_OF.get(_act_names[aid], "other"), it.get("usageTotalUsd") or 0.0
            month[g] = month.get(g, 0.0) + usd
            if it["startedAt"][:10] == now.strftime("%Y-%m-%d"):
                day += usd
        _spend["v"] = {"month": month, "day": day}
    except Exception:
        _spend["v"] = None
    _spend["at"] = time.time()
    return _spend["v"]


def monthly_usage(token: str) -> float | None:
    """This month's Apify spend (USD), cached 60 s (reset after every run); None when the API can't tell → run() refuses."""
    if time.time() - _usage["at"] > 60:
        try:
            d = httpx.get("https://api.apify.com/v2/users/me/limits", params={"token": token}, timeout=15).json()["data"]
            _usage["usd"] = float(d["current"]["monthlyUsageUsd"])
        except Exception:
            _usage["usd"] = None
        _usage["at"] = time.time()
    return _usage["usd"]


class Apify1688Connector(_Apify):
    key = "apify_1688"
    name = "1688 (Apify · giá xưởng TQ)"
    group = "marketplace"
    actor = "zen-studio/1688-wholesale-scraper"
    max_items = 20  # ≈ $0.10 / search (the free Apify plan also caps this actor at 20)

    def fetch_ads(self, params: FetchParams) -> Iterator[AdRecord]:
        if not params.query:
            return
        items = self.run({"keywords": [params.query], "maxResults": self.cap(params)})
        for pos, it in enumerate(items, 1):
            if not it.get("offerId"):
                continue
            sup = it.get("supplier") or {}
            price = it.get("price") or {}
            url = it.get("detailUrl") or f"https://detail.1688.com/offer/{it['offerId']}.html"
            yield AdRecord(
                source=self.key, source_ad_id=str(it["offerId"]), platform="1688", platforms=["1688"],
                title=it.get("title"), product_name=_short(it.get("title")), ad_text=it.get("title"), creative_type="image",
                media=[MediaItem("image", u) for u in (it.get("images") or [])[:3]],
                advertiser=sup.get("companyName"), advertiser_url=sup.get("shopUrl"),
                landing_page=url, product_url=url, price=_f(price.get("min")), currency=price.get("currency") or "CNY",
                sold_count=_num(it.get("soldDisplay")) or it.get("orderCount"), rank=pos, active=True, snapshot_url=url,
                matched_query=params.query,
                raw_source={k: it.get(k) for k in ("offerId", "title", "price", "soldDisplay", "orderCount", "province", "city",
                                                   "repurchaseRate", "tags")} | {"supplier": sup.get("companyName")},
            )


class ApifyTaobaoConnector(_Apify):
    key = "apify_taobao"
    name = "Taobao / Tmall (Apify · giá & đã bán TQ)"
    group = "marketplace"
    actor = "zen-studio/taobao-search-scraper"
    max_items = 10  # actor minimum; ≈ $0.11 / search (start fee $0.05)

    def fetch_ads(self, params: FetchParams) -> Iterator[AdRecord]:
        if not params.query:
            return
        items = self.run({"keyword": params.query, "maxItems": max(10, self.cap(params)),  # actor minimum is 10
                          "sort": "sales", "enrichWithDetails": False})
        for pos, it in enumerate(items, 1):
            if not it.get("itemId"):
                continue
            shop = it.get("shop") or {}
            url = it.get("url") or f"https://item.taobao.com/item.htm?id={it['itemId']}"
            title = it.get("titleEn") or it.get("title")
            pics = [_pic(p) for p in (it.get("mainPictureUrl"), *(it.get("pictures") or []))]
            yield AdRecord(
                source=self.key, source_ad_id=str(it["itemId"]), platform="taobao", platforms=["taobao"],
                title=it.get("title"), product_name=_short(title), ad_text=title, creative_type="image",
                media=[MediaItem("image", u) for u in dict.fromkeys(p for p in pics if p)][:3],
                advertiser=shop.get("shopNameFromSearch") or shop.get("shopName"),
                landing_page=url, product_url=url, price=_f(it.get("price") or it.get("priceFromSearch")),
                original_price=_f(it.get("originalPrice")), currency=it.get("priceCurrency") or "CNY",
                sold_count=it.get("totalSold") or it.get("sales") or _num(it.get("salesSignal")), rank=pos, active=True,
                snapshot_url=url, matched_query=params.query,
                raw_source={k: it.get(k) for k in ("itemId", "title", "price", "salesSignal", "isTmall", "sellerGoodrat")},
            )


class ApifyTikTokTopAdsConnector(_Apify):
    key = "apify_tiktok_top_ads"
    name = "TikTok Ads · Creative Center Top Ads (Apify) — PH/US/EU/ME"
    group = "ad_intel"
    actor = "lexis-solutions/tiktok-top-ads-scraper"
    max_items = 20  # ≈ $0.08 / search

    def fetch_ads(self, params: FetchParams) -> Iterator[AdRecord]:
        if not params.query:
            return  # the actor fails without a keyword
        countries = [c for c in params.countries or [] if c and c.upper() != "ALL"]
        inp = {"keyword": params.query, "period": str(self.config.get("period") or "180"), "maxItems": self.cap(params)}
        if countries:
            inp["country"] = countries
        if params.media_type == "image":
            return  # Top Ads are videos only
        for it in self.run(inp):
            if not it.get("id"):
                continue
            video = (it.get("videoUrls") or {})
            url = video.get("720p") or next(iter(video.values()), None)
            cc = it.get("countryCodes") or []
            yield AdRecord(
                source=self.key, source_ad_id=str(it["id"]), platform="tiktok", platforms=["tiktok"],
                country=next((c for c in countries if c in cc), cc[0] if cc else None), countries=cc,
                advertiser=it.get("brandName"), ad_text=it.get("title"), title=it.get("title"), creative_type="video",
                media=[MediaItem("video", url, preview_url=it.get("videoCover"), width=it.get("videoWidth"),
                                 height=it.get("videoHeight"), duration_sec=it.get("videoDuration"))] if url else [],
                landing_page=it.get("landingPage"), likes=it.get("likes"), comments=it.get("comments"),
                shares=it.get("shares"), active=True, matched_query=params.query,
                snapshot_url=f"https://ads.tiktok.com/business/creativecenter/topads/{it['id']}/pc/en",
                raw_source={k: it.get(k) for k in ("id", "title", "brandName", "ctr", "cost", "industryKey", "objectiveKey",
                                                   "countryCodes", "likes", "source")},
            )


_EU = {"AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IS", "IE", "IT", "LV", "LI", "LT", "LU",
       "MT", "NL", "NO", "PL", "PT", "RO", "SK", "SI", "ES", "SE", "CH", "GB"}


class ApifyTikTokLibraryConnector(_Apify):
    key = "apify_tiktok_ads"
    name = "TikTok Ads · Ad Library EU/UK (Apify)"
    group = "transparency"
    actor = "lexis-solutions/tiktok-ads-scraper"

    def fetch_ads(self, params: FetchParams) -> Iterator[AdRecord]:
        if not params.query:
            return
        countries = [c.upper() for c in params.countries or [] if c.upper() in _EU] or ["all"]
        for country in countries:  # the library only covers ads delivered in the EU/EEA/UK/CH
            for it in self.run({"query": params.query, "country": country, "maxPages": max(1, self.cap(params) // 12)}):
                if not it.get("adId"):
                    continue
                regions = [t.get("region") for t in it.get("targetingByLocation") or [] if t.get("region")]
                media = [MediaItem("video", it["adVideoUrl"], preview_url=it.get("adVideoCover"))] if it.get("adVideoUrl") else []
                media += [MediaItem("image", u) for u in it.get("adImageUrls") or []]
                yield AdRecord(  # same library + same ad ids as the free connector: one row per ad, not two
                    source="tiktok_ad_library", source_ad_id=str(it["adId"]), platform="tiktok", platforms=["tiktok"],
                    country=regions[0] if regions else None, countries=regions,
                    advertiser=it.get("advertiserName"), ad_text=it.get("adTitle"), title=it.get("adTitle"),
                    creative_type=media[0].type if media else None, media=media, landing_page=it.get("adLandingUrl"),
                    first_seen=_ms(it.get("adStartDate")), last_seen=_ms(it.get("adEndDate")),
                    active=it.get("status") == "active", impressions_text=it.get("adImpressions"),
                    snapshot_url=f"https://library.tiktok.com/ads/detail/?ad_id={it['adId']}", matched_query=params.query,
                    raw_source={k: it.get(k) for k in ("adId", "adTitle", "advertiserName", "advertiserLocation", "adImpressions",
                                                       "adEstimatedAudience", "status")},
                )

