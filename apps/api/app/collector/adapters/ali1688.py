"""1688 hybrid source (docs/ToolSpy_1688_Hybrid_Connector.md): official 1688 AI API first, Apify actor as paid fallback.

Primary: the signed AK API the 1688 AI app hands out (reference/1688-shopkeeper, `search` capability) —
POST https://ainext.1688.com/1688claw/skill/searchoffer, HMAC-SHA256 over x-csk-* headers. Free ("限时免费"), ≤ 20 offers,
with real trade stats (30-day sales, good / repurchase rate, downstream listings, 24h pickup rate).
AK: connector config `ak`, else ALI_1688_AK in .env (get it in the 1688 AI app → 一键部署开店Claw).

Fallback: Apify1688Connector, only when the live search also selected `apify_1688` (realtime._run_search hands its
config in as `apify_fallback`) and the AK source failed or returned < `min_results`. Apify's own budget guards apply.
No Playwright scraping of 1688.com: plain requests hit a login wall / captcha (marketplaces.py, 2026-10-06) and the
design forbids working around those.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time
import uuid
from typing import Iterator

import httpx

from ..base import AuthExpired, BaseConnector, ConnectorError, FetchParams, NotConfigured, RateLimit
from ..contract import AdRecord, MediaItem
from .apify import Apify1688Connector, _f, _num, _short

log = logging.getLogger(__name__)

BASE_URL = "https://ainext.1688.com"
SEARCH_PATH = "/1688claw/skill/searchoffer"
SKILL_VERSION = "1.0.1"  # reference/1688-shopkeeper/scripts/_const.py — sent signed in x-csk-version


def split_ak(raw: str) -> tuple[str, str] | None:
    """AK = base64(secret[32] + id) or the plain string; → (ak_id, ak_secret)."""
    try:
        raw = base64.urlsafe_b64decode(raw).decode() or raw
    except Exception:
        pass
    return (raw[32:], raw[:32]) if raw and len(raw) > 32 else None


def sign(path: str, body: str, ak_id: str, ak_secret: str, ts: str | None = None, nonce: str | None = None) -> dict:
    """Headers for a signed POST (same canonical string as the reference _auth.build_signature; no query string)."""
    md5 = base64.b64encode(hashlib.md5(body.encode()).digest()).decode() if body else ""
    csk = {"x-csk-ak": ak_id, "x-csk-time": ts or str(int(time.time())), "x-csk-nonce": nonce or uuid.uuid4().hex[:8],
           "x-csk-content-md5": md5, "x-csk-version": SKILL_VERSION}
    canon = "".join(f"{k}:{csk[k].strip()}\n" for k in sorted(csk))
    to_sign = f"POST\n{md5}\napplication/json\n{csk['x-csk-time']}\n{canon}{path}"
    sig = base64.b64encode(hmac.new(ak_secret.encode(), to_sign.encode(), hashlib.sha256).digest()).decode()
    return {"Content-Type": "application/json", "x-csk-sign": sig, **csk}


def _count(v) -> int | None:
    """Trade stats come as numbers or banded strings: 1268, "20+", "300+", "<10" (→ None: below the reported band)."""
    if isinstance(v, (int, float)):
        return int(v)
    return None if not isinstance(v, str) or v.strip().startswith("<") else _num(v)


def parse_offers(model: dict, query: str, limit: int, source: str = "ali1688") -> list[AdRecord]:
    """searchoffer `model.data` = {offer_id: {title, price, image, stats}} → commerce AdRecords. Missing fields stay None."""
    data = model.get("data") if isinstance(model, dict) else None
    if not isinstance(data, dict):
        raise ConnectorError("1688 AK: kết quả tìm kiếm sai định dạng (model.data không phải object)")
    out = []
    for pos, (oid, it) in enumerate(list(data.items())[:limit], 1):
        if not isinstance(it, dict) or not str(oid).strip():
            continue
        s = it.get("stats") if isinstance(it.get("stats"), dict) else {}
        url = f"https://detail.1688.com/offer/{oid}.html"
        img = it.get("image") if isinstance(it.get("image"), str) and it.get("image") else None
        img = "https:" + img if img and img.startswith("//") else img
        sold = _count(s.get("last30DaysSales"))
        out.append(AdRecord(
            source=source, source_ad_id=str(oid), platform="1688", platforms=["1688"],
            title=it.get("title"), product_name=_short(it.get("title")), ad_text=it.get("title"), creative_type="image",
            media=[MediaItem("image", img)] if img else [], landing_page=url, product_url=url,
            price=_f(it.get("price")), currency="CNY", sold_count=sold,
            review_count=_count(s.get("remarkCnt")),
            rank=pos, active=True, snapshot_url=url, matched_query=query,
            raw_source={"offerId": str(oid), "title": it.get("title"), "price": it.get("price"), "stats": s or None},
        ))
    return out


class Ali1688Connector(BaseConnector):
    key = "ali1688"
    name = "1688 (API chính thức · AK) — fallback Apify"
    kind = "official_api"
    group = "marketplace"
    rate_limit = RateLimit(requests_per_minute=20, retries=0)  # retried in search(): every attempt needs a fresh nonce
    supports_search = True
    max_items = 20  # the API returns ≤ 20 offers per query and has no paging…
    max_total = 100
    # …but it reads natural language: each suffix surfaces a different slice (measured: 10 variants of "gold bracelet" = 141
    # distinct offers). Ordered by how many new offers each added.
    VARIANTS = ("", " 爆款", " 低价批发", " 一件代发", " 新品", " 销量高")
    config_fields = [
        {"key": "ak", "label": "ALI_1688_AK (để trống = lấy từ .env)", "secret": True},
        {"key": "min_results", "label": "Ít hơn N kết quả → coi là thiếu dữ liệu (fallback Apify nếu được chọn), mặc định 5"},
    ]

    def search(self, query: str, limit: int) -> list[AdRecord]:
        keys = split_ak(self.config.get("ak") or os.getenv("ALI_1688_AK") or "")
        if not keys:
            raise NotConfigured("Thiếu ALI_1688_AK (.env hoặc cấu hình connector) — lấy AK trong app 1688 AI版")
        body = json.dumps({"query": query, "channel": ""})
        for attempt in range(3):  # like the reference: retry network / 429 / 5xx only, re-signed each time
            try:
                res = self.request("POST", BASE_URL + SEARCH_PATH, content=body, headers=sign(SEARCH_PATH, body, *keys),
                                   timeout=30).json()
                break
            except AuthExpired:
                raise
            except ConnectorError:
                if attempt == 2:
                    raise
                time.sleep(2 ** attempt)
            except (httpx.HTTPError, ValueError) as e:  # 400 / non-JSON body: a failure the fallback must see
                raise ConnectorError(f"1688 AK: {type(e).__name__}: {e}") from None
        if not isinstance(res, dict) or res.get("success") is False:
            err = res.get("msgInfo") or res.get("msgCode") if isinstance(res, dict) else None
            raise ConnectorError(f"1688 AK: {err or 'phản hồi không hợp lệ'}")
        return parse_offers(res.get("model"), query, limit, self.key)

    def fetch_ads(self, params: FetchParams) -> Iterator[AdRecord]:
        q = (params.query or "").strip()
        if not q:
            return
        total = max(1, min(params.limit, self.max_total))
        limit = min(total, self.max_items)
        mr = str(self.config.get("min_results") or "").strip()
        need = min(int(mr) if mr.isdigit() else 5, limit)
        recs, why = [], None
        seen_ids: set[str] = set()
        for i in range(-(-total // self.max_items)):  # one query per 20 wanted, each a different variant; yielded as they arrive
            if len(recs) >= total or i >= len(self.VARIANTS):
                break
            try:
                batch = self.search(q + self.VARIANTS[i], limit)
            except ConnectorError as e:
                why = str(e)
                break
            for r in batch:
                if r.source_ad_id not in seen_ids and len(recs) < total:
                    seen_ids.add(r.source_ad_id)
                    r.rank = len(recs) + 1
                    recs.append(r)
                    yield r
        if not why and len(recs) < need:
            why = f"chỉ {len(recs)} kết quả (< {need})"
        if not why:
            return
        fb = self.config.get("apify_fallback")
        if fb is None:  # Apify not selected for this search: report the AK problem, never spend
            if not recs:
                raise ConnectorError(f"{why} — chọn thêm Apify 1688 để dùng nguồn trả phí")
            return
        log.warning("1688 '%s': AK source insufficient (%s) → Apify fallback", q, why)
        seen = set(seen_ids)
        try:
            for r in Apify1688Connector(fb).fetch_ads(params):  # same offerId from both sources = one listing
                if r.source_ad_id not in seen:
                    yield r
        except ConnectorError as e:
            if not recs:  # nothing from either source → the job reports it
                raise ConnectorError(f"1688 AK: {why}; Apify: {e}") from None
            # keep the AK listings already yielded: an exception here would drop the worker's unflushed batch
            log.warning("1688 '%s': Apify fallback failed (%s) — keeping %d AK listings", q, e, len(recs))
