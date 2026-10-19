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
import re
import time
import uuid
from dataclasses import replace
from typing import Iterator

import httpx

from ..base import AuthExpired, BaseConnector, ConnectorError, FetchParams, NotConfigured, RateLimit
from ..contract import AdRecord, MediaItem
from .apify import Apify1688Connector, _f, _num, _short

log = logging.getLogger(__name__)

BASE_URL = "https://ainext.1688.com"
SEARCH_PATH = "/1688claw/skill/searchoffer"
SKILL_VERSION = "1.0.1"  # reference/1688-shopkeeper/scripts/_const.py — sent signed in x-csk-version


class _InvalidResponse(ConnectorError):
    """Incomplete upstream response: retry with a fresh signed nonce, never call it empty."""


class _RetryableBusinessError(ConnectorError):
    pass


def _business_error(payload: dict) -> ConnectorError:
    code = str(payload.get("msgCode") or payload.get("code") or "")
    message = payload.get("message") or payload.get("msgInfo") or payload.get("msg") or payload.get("errorMessage") or code or "nguồn báo thất bại"
    detail = f"1688 AK: {str(message)[:180]}"
    if re.search(r"\b(401|403)\b", code):
        return AuthExpired(detail)
    if re.search(r"\b(429|5\d\d)\b", code):
        return _RetryableBusinessError(detail)
    return ConnectorError(detail)


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
    if not isinstance(model, dict):
        raise _InvalidResponse("1688 AK: phản hồi thiếu model.data")
    if model.get("success") is False:
        raise _business_error(model)
    if "data" not in model:
        raise _InvalidResponse("1688 AK: phản hồi thiếu model.data")
    data = model["data"]
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError:
            raise _InvalidResponse("1688 AK: model.data không phải JSON hợp lệ") from None
    if data is None and model.get("success") is True:
        return []
    if isinstance(data, list):
        pairs = []
        for it in data:
            if not isinstance(it, dict):
                raise _InvalidResponse("1688 AK: danh sách sản phẩm có phần tử sai định dạng")
            oid = it.get("offerId") or it.get("offer_id") or it.get("id")
            if oid is None:
                raise _InvalidResponse("1688 AK: sản phẩm thiếu offerId")
            pairs.append((oid, it))
    elif isinstance(data, dict):
        pairs = list(data.items())
    else:
        raise _InvalidResponse(f"1688 AK: model.data có kiểu không hỗ trợ ({type(data).__name__})")
    out = []
    for oid, it in pairs:
        if len(out) >= max(0, limit):
            break
        if not isinstance(it, dict) or not str(oid).strip().isdigit():
            raise _InvalidResponse("1688 AK: sản phẩm hoặc offerId sai định dạng")
        oid = str(oid).strip()
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
            rank=len(out) + 1, active=True, snapshot_url=url, matched_query=query,
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
        for attempt in range(3):  # transport / 429 / 5xx / incomplete responses; every attempt re-signed
            try:
                response = self.request("POST", BASE_URL + SEARCH_PATH, content=body,
                                        headers=sign(SEARCH_PATH, body, *keys), timeout=30)
            except AuthExpired:
                raise
            except ConnectorError as e:
                last_error = e  # BaseConnector wraps transport / HTTP 429 / 5xx errors.
            except httpx.HTTPError as e:
                raise ConnectorError(f"1688 AK: {type(e).__name__}: {e}") from None
            else:
                try:
                    res = response.json()
                    if not isinstance(res, dict):
                        raise _InvalidResponse("1688 AK: phản hồi không phải object")
                    if res.get("success") is False:
                        raise _business_error(res)
                    return parse_offers(res.get("model"), query, limit, self.key)
                except (_InvalidResponse, _RetryableBusinessError) as e:
                    last_error = e
                except ValueError:
                    last_error = _InvalidResponse("1688 AK: phản hồi không phải JSON hợp lệ")
            if attempt == 2:
                raise last_error
            log.warning("1688 AK: phản hồi chưa dùng được, thử lại %d/3 (%s)", attempt + 2, type(last_error).__name__)
            time.sleep(2 ** attempt)

    def fetch_page(self, params: FetchParams, country: str, state: dict | None = None) -> tuple[list[AdRecord], dict]:
        """Live-search paging: one API call (≤ 20 offers, one VARIANTS suffix) per page, so a results page of 50 costs 3 calls
        instead of the whole crawl, and "Trang sau" continues from the next suffix. state = {i: next suffix, seen: offer ids, done}."""
        st = dict(state or {})
        q, i, seen = (params.query or "").strip(), int(st.get("i", 0)), list(st.get("seen", []))
        if not q or i >= len(self.VARIANTS):
            return [], {**st, "done": True}
        try:
            batch = self.search(q + self.VARIANTS[i], self.max_items)
            why = None
        except ConnectorError as e:
            batch, why = [], str(e)
        fresh = [r for r in batch if r.source_ad_id not in seen]
        for r in fresh:
            seen.append(r.source_ad_id)
            r.rank = len(seen)
        st = {"i": i + 1, "seen": seen, "done": i + 1 >= len(self.VARIANTS)}
        if i == 0 and not why and len(batch) < min(int(str(self.config.get("min_results") or "").strip() or 5), self.max_items):
            why = f"chỉ {len(batch)} kết quả"
        if not why:
            return fresh, st
        fb = self.config.get("apify_fallback")  # first page only; later pages keep what the AK source gave
        if i > 0 or fb is None:
            if fresh:
                return fresh, st
            # nothing new: raise instead of ending the paging — the saved cursor stays on this suffix, "Trang sau" retries it
            raise ConnectorError(why if i > 0 else f"{why} — chọn thêm Apify 1688 để dùng nguồn trả phí")
        log.warning("1688 '%s': AK source insufficient (%s) → Apify fallback", q, why)
        try:
            extra = [r for r in Apify1688Connector(fb).fetch_ads(replace(params, limit=params.limit - len(fresh))) if r.source_ad_id not in seen]
        except ConnectorError as e:
            if not fresh:
                raise ConnectorError(f"1688 AK: {why}; Apify: {e}") from None
            extra = []
        return fresh + extra, {**st, "done": True}  # Apify is single-shot: no further pages

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
            remaining = total - len(recs)
            for r in Apify1688Connector(fb).fetch_ads(replace(params, limit=remaining)):
                if r.source_ad_id not in seen:
                    seen.add(r.source_ad_id)
                    r.rank = len(seen)
                    yield r
                    if len(seen) >= total:
                        break
        except ConnectorError as e:
            if not recs:  # nothing from either source → the job reports it
                raise ConnectorError(f"1688 AK: {why}; Apify: {e}") from None
            # keep the AK listings already yielded: an exception here would drop the worker's unflushed batch
            log.warning("1688 '%s': Apify fallback failed (%s) — keeping %d AK listings", q, e, len(recs))
