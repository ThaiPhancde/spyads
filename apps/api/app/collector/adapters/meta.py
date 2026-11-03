"""Meta connectors.

* MetaLibraryConnector — public Ad Library (all countries, commercial ads, with video/image URLs)
  via the open-source `meta-ads-collector` package (MIT, © 2025 Yossef). It talks to the same
  GraphQL endpoint the Ad Library website uses; no login needed. Unofficial: Meta can change it
  (doc_id) at any time → health check + alert. Keep volume modest and respect Meta's terms.
* MetaGraphConnector — official Graph API `ads_archive`. Stable, but Meta only returns
  commercial ads for ads delivered in the EU/UK (DSA) plus political/issue ads elsewhere.
* ApifyMetaConnector — paid managed scraper (Apify actor) for production volume.
"""
from __future__ import annotations

import hashlib
import logging
import math
import re
from datetime import datetime, timezone
from typing import Iterator

from ..base import BaseConnector, Blocked, ConnectorError, FetchParams, NotConfigured, RateLimit, RateLimited
from ..contract import AdRecord, MediaItem

log = logging.getLogger(__name__)
RESUME_KEYS = ("cursor", "sid", "tok", "seen")  # what a resumed traversal carries between runs

_PLACEHOLDER = re.compile(r"\{\{[^}]+\}\}")


def _clean(s) -> str | None:
    if not isinstance(s, str):
        return None
    s = _PLACEHOLDER.sub("", s).strip()
    return s or None


def _ts(v) -> str | None:
    if v in (None, "", 0):
        return None
    if isinstance(v, (int, float)):
        return datetime.fromtimestamp(v, tz=timezone.utc).replace(tzinfo=None).isoformat()
    return str(v)


def _impressions(s: dict) -> str | None:
    iw = s.get("impressions_with_index") or {}
    return (iw.get("impressions_text") if isinstance(iw, dict) else None) or s.get("impressions_text") or None


def _spend(s: dict) -> str | None:
    sp = s.get("spend")
    if isinstance(sp, dict) and (sp.get("lower_bound") or sp.get("upper_bound")):
        return f"{sp.get('lower_bound') or '?'}-{sp.get('upper_bound') or '?'} {s.get('currency') or ''}".strip()
    return None


def _reach(s: dict) -> int | None:
    aaa = s.get("aaa_info") or {}
    for v in (aaa.get("eu_total_reach") if isinstance(aaa, dict) else None, s.get("eu_total_reach"), s.get("reach_estimate")):
        if isinstance(v, (int, float)) and v > 0:
            return int(v)
    return None


_CHALLENGE = re.compile(r"fetch\('(/__rd_verify_[^']+)'")


def _balanced(html: str, key: str) -> str | None:
    """The balanced {...} JSON object following `key` (from reference/AdsLibrary, MIT)."""
    i = html.find(key)
    i = html.find("{", i) if i >= 0 else -1
    if i < 0:
        return None
    depth, in_str, esc = 0, False, False
    for j in range(i, len(html)):
        c = html[j]
        if esc:
            esc = False
        elif c == "\\":
            esc = in_str
        elif c == '"':
            in_str = not in_str
        elif not in_str:
            depth += (c == "{") - (c == "}")
            if depth == 0:
                return html[i:j + 1]
    return None


def ssr_search(query: str, country: str, active_only: bool, page_ids: list[str] | None, media_type: str | None,
               proxy: str | None = None) -> list[dict]:
    """Fallback when the GraphQL doc_id rotates: the Ad Library server-renders page 1 (~30 ads) as a Relay payload.
    No doc_id needed, and no political filter — commercial only. ponytail: first page only, no cursor."""
    import json
    import requests

    params = {"active_status": "active" if active_only else "all", "ad_type": "all", "country": country,
              "media_type": media_type or "all"}
    params.update({"view_all_page_id": page_ids[0], "search_type": "page"} if page_ids
                  else {"q": query, "search_type": "keyword_unordered"})
    url = "https://www.facebook.com/ads/library/"
    with requests.Session() as ses:
        ses.headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/115.0"
        if proxy:
            h, po, *cred = proxy.split(":")
            ses.proxies = {"https": f"http://{':'.join(cred[:1])}:{':'.join(cred[1:])}@{h}:{po}" if cred else f"http://{h}:{po}"}
        r = ses.get(url, params=params, timeout=30)
        m = _CHALLENGE.search(r.text)  # stop at access challenges
        if m:
            raise Blocked("Meta access challenge; user action required")
    blob = _balanced(r.text, '"search_results_connection":')
    r.raise_for_status()
    if not blob:
        raise ConnectorError("Meta SSR response missing search results")
    out = []
    for edge in json.loads(blob).get("edges", []):
        node = edge.get("node", {})
        out += [x for x in node.get("collated_results") or [node] if x.get("ad_archive_id")]
    return out


def extract_ads(payload) -> list[dict]:
    """Shape-agnostic fallback (sonda-imperial parser): every dict holding ad_archive_id + snapshot is an ad."""
    out: list[dict] = []

    def walk(n, depth=0):
        if depth > 40:
            return
        if isinstance(n, dict):
            if n.get("ad_archive_id") and ("snapshot" in n or "page_id" in n):
                out.append(n)
                return
            for v in n.values():
                walk(v, depth + 1)
        elif isinstance(n, list):
            for v in n:
                walk(v, depth + 1)

    walk(payload)
    return out


def map_library_ad(raw: dict, source: str, country: str | None) -> AdRecord:
    """Map one Ad Library `collated_results[]` item (or Apify item, same shape) to the contract."""
    s = {**raw, **(raw.get("snapshot") or {})}
    country = country.upper() if country else None
    body = s.get("body")
    text = _clean(body.get("text") if isinstance(body, dict) else body)
    cards = s.get("cards") or []
    if not text and cards:
        text = _clean(cards[0].get("body"))
    media: list[MediaItem] = []
    for v in (s.get("videos") or []) + [c for c in cards if c.get("video_hd_url") or c.get("video_sd_url")]:
        url = v.get("video_hd_url") or v.get("video_sd_url")
        if url:
            media.append(MediaItem("video", url, preview_url=v.get("video_preview_image_url"),
                                   quality="hd" if v.get("video_hd_url") else "sd", sd_url=v.get("video_sd_url")))
    for im in (s.get("images") or []) + [c for c in cards if not (c.get("video_hd_url") or c.get("video_sd_url"))]:
        url = im.get("original_image_url") or im.get("resized_image_url")
        if url:
            media.append(MediaItem("image", url))
    fmt = (s.get("display_format") or "").upper()
    ctype = "video" if fmt == "VIDEO" or (media and media[0].type == "video") else \
        "carousel" if fmt in ("CAROUSEL", "DPA", "DCO") and len(cards) > 1 else "image" if media else None
    platforms = s.get("publisher_platform") or s.get("publisher_platforms") or []
    countries = [str(c).upper() for c in s.get("targeted_or_reached_countries") or []]
    title = _clean(s.get("title")) or (_clean(cards[0].get("title")) if cards else None)
    link = s.get("link_url") or (cards[0].get("link_url") if cards else None)
    ad_id = str(s.get("ad_archive_id") or s.get("adArchiveID") or s.get("id"))
    return AdRecord(
        source=source, source_ad_id=ad_id,
        platform=(platforms[0].lower() if platforms else "facebook"),
        platforms=[str(p).lower() for p in platforms],
        country=(country if country and country != "ALL" else (countries[0] if countries else None)),
        countries=sorted(set(countries) | ({country.upper()} if country else set())),  # "ALL" = worldwide search
        page_id=str(s.get("page_id")) if s.get("page_id") else None,
        advertiser=s.get("page_name"), advertiser_url=s.get("page_profile_uri"),
        ad_text=text, title=title, cta_type=s.get("cta_type"), cta_text=s.get("cta_text"),
        creative_type=ctype, media=media, landing_page=link,
        product_name=title, product_url=link,
        first_seen=_ts(s.get("start_date") or s.get("ad_delivery_start_time")),
        last_seen=_ts(s.get("end_date") or s.get("ad_delivery_stop_time")),
        active=s.get("is_active"), variants=s.get("collation_count"),
        likes=None,  # Ad Library does not expose per-ad engagement (page_like_count stays in raw_source)
        impressions_text=_impressions(s), spend_text=_spend(s), reach=_reach(s),
        page_likes=s.get("page_like_count") if isinstance(s.get("page_like_count"), int) else None,
        snapshot_url=f"https://www.facebook.com/ads/library/?id={ad_id}",
        raw_source=raw,
    )


class MetaLibraryConnector(BaseConnector):
    key = "meta_library"
    name = "Meta Ad Library (public)"
    kind = "browser"
    group = "transparency"
    rate_limit = RateLimit(requests_per_minute=20, retries=3)
    supports_search = True
    config_fields = [
        {"key": "proxy", "label": "Proxy (tuỳ chọn) host:port:user:pass", "secret": True},
        {"key": "keywords", "label": "Từ khoá theo dõi (mỗi dòng 1 từ)"},
        {"key": "countries", "label": "Quốc gia (VD: SA,AE,VN)"},
        {"key": "page_ids", "label": "Page ID đối thủ theo dõi (phẩy)"},
        {"key": "max_per_query", "label": "Số ad tối đa mỗi từ khoá (mặc định 60)"},
    ]

    def authenticate(self):
        try:
            import meta_ads_collector  # noqa: F401
        except ImportError as e:
            raise NotConfigured("Cần `pip install meta-ads-collector`") from e

    def _collector(self):
        from meta_ads_collector import MetaAdsCollector

        return MetaAdsCollector(proxy=self.config.get("proxy") or None, rate_limit_delay=2.0, jitter=1.0, max_retries=3)

    def fetch_ads(self, params: FetchParams) -> Iterator[AdRecord]:
        """Scheduled / single-shot path = fetch_page in a loop: the collector's own `search()` rejects country 'ALL'
        (tracked queries on 'ALL' failed every run) and skipped the token bucket."""
        resume, out = params.extra.get("resume") or {}, params.extra.setdefault("resume_out", {})
        # pages the media filter empties never raise `got`: without a budget the loop walked every page the source had
        budget = params.extra.get("max_pages") or max(5, 3 * math.ceil(params.limit / 30))
        for country in params.countries or ["ALL"]:
            st = {k: v for k, v in (resume.get(country) or {}).items() if k in RESUME_KEYS}  # continue deeper
            resumed, got, empty, pages = bool(st.get("cursor")), 0, 0, 0
            while got < params.limit and not st.get("done") and pages < budget:
                try:
                    recs, st = self.fetch_page(params, country, st)
                except (Blocked, RateLimited):
                    raise  # not an expired cursor: dropping it and asking page 1 again would only make it worse
                except ConnectorError:
                    if not resumed:
                        raise
                    st, resumed = {}, False  # Meta refused the stored cursor (expired session): start over from page 1
                    continue
                pages += 1
                out[country] = {k: st.get(k) for k in (*RESUME_KEYS, "done")}
                empty = 0 if recs else empty + 1  # Meta sends empty pages mid-stream; pause after several; only page_info declares the end
                stop = empty >= 3 or st.get("page_status") in ("partial", "cycle")
                for rec in recs:
                    if params.media_type and rec.creative_type != params.media_type:
                        continue
                    got += 1
                    yield rec
                    # Finish the whole fetched page before exposing its next cursor.
                if stop:
                    break  # this page's ads are out; pausing never claims the end

    def fetch_page(self, params: FetchParams, country: str, state: dict | None = None) -> tuple[list[AdRecord], dict]:
        """One page (~10 ads) with a resume cursor, so the UI can 'load more' from where it stopped.
        state = {"cursor", "sid", "tok", "done"} — keep it and pass it back for the next page."""
        import uuid

        self.authenticate()
        st = dict(state or {})
        if st.get("done"):
            return [], st
        st.setdefault("sid", str(uuid.uuid4()))
        st.setdefault("tok", str(uuid.uuid4()))
        col = getattr(self, "_col", None)
        if col is None:
            col = self._col = self._collector()
            col.client.initialize()
        self.bucket.take()
        try:
            resp, cursor = col.client.search_ads(
                query=params.query or "", country=country.upper(), active_status="ACTIVE" if params.active_only else "ALL",
                media_type={"video": "VIDEO", "image": "IMAGE"}.get(params.media_type or "", "ALL"),
                search_type="PAGE" if params.page_ids else "KEYWORD_UNORDERED", page_ids=params.page_ids or None,
                cursor=st.get("cursor"), first=30, sort_mode=None, session_id=st["sid"], collation_token=st["tok"])
        except Exception as e:
            if params.extra.get("competitor_strict"):
                raise ConnectorError("Meta advertiser query failed; no access-challenge fallback") from e
            if st.get("cursor"):
                raise ConnectorError(f"Meta Ad Library: {type(e).__name__}: {e}") from e
            try:  # GraphQL broke on page 1 → server-rendered payload (first page only)
                raw_ads = ssr_search(params.query or "", country.upper(), params.active_only, params.page_ids,
                                     params.media_type, self.config.get("proxy") or None)
            except Blocked:
                raise
            except Exception as e2:
                raise ConnectorError(f"Meta Ad Library: {type(e).__name__}: {e}; SSR: {type(e2).__name__}: {e2}") from e
            # SSR has page 1 and no cursor: not the end of the results — "partial" pauses the loop, the next run retries GraphQL
            recs = [map_library_ad(raw, self.key, country) for raw in raw_ads]
            st.update(cursor=None, done=False, page_status="partial")
            self.pages_fetched = getattr(self, "pages_fetched", 0) + 1
            return recs, st
        if isinstance(resp, dict) and resp.get("rate_limited"):
            raise RateLimited("Meta Ad Library rate limit")
        if not isinstance(resp, dict) or any(resp.get(k) for k in ("error", "errors", "session_expired")):
            raise ConnectorError("Meta source returned an error, rate limit, or expired session")
        if not isinstance(resp.get("ads"), list):
            raise ConnectorError("Meta advertiser response missing ads list")
        items = resp["ads"]
        if not resp.get("page_info"):
            raise ConnectorError("Meta response missing pagination metadata")
        # collation siblings are kept as records: ingest maps them onto one ad via meta_collations (across pages and jobs)
        recs = [map_library_ad(raw, self.key, country) for raw in items]
        info = resp.get("page_info") or {}
        has_next = info.get("has_next_page", info.get("hasNextPage", bool(cursor)))
        if has_next and (not cursor or cursor == st.get("cursor")):
            raise ConnectorError("Meta pagination did not advance")
        self.pages_fetched = getattr(self, "pages_fetched", 0) + 1  # Data & Connectors KPIs (crawl_runs.pages)
        self.empty_pages = getattr(self, "empty_pages", 0) + (not recs)
        # A → B → A: following an earlier cursor repeats the same pages for ever (1,000 requests for 3 ads, measured in a test).
        # This page's ads are good; the traversal pauses and the next run starts over — never "the end".
        seen = st.get("seen") or []
        mark = hashlib.sha1(cursor.encode()).hexdigest()[:12] if has_next and cursor else None
        if mark and mark in seen:
            self.cursor_cycles = getattr(self, "cursor_cycles", 0) + 1
            log.warning("Meta pagination cycle (%s, %s): pausing, next run starts from page 1", params.query or params.page_ids, country)
            st.update(cursor=None, done=False, page_status="cycle", seen=[])
            return recs, st
        st["seen"] = (seen + [mark])[-200:] if mark else seen  # ponytail: last 200 cursors, a longer cycle hits the page budget
        st["cursor"] = cursor
        st["done"] = not has_next
        st["page_status"] = "end" if st["done"] else "page" if recs else "empty"
        return recs, st

    def health_check(self) -> dict:
        recs = list(self.fetch_ads(FetchParams(query="shop", countries=["US"], limit=2)))
        return {"status": "healthy" if recs else "degraded", "sample": len(recs)}


class MetaGraphConnector(BaseConnector):
    key = "meta_graph"
    name = "Meta Ad Library API (official)"
    kind = "official_api"
    group = "transparency"
    rate_limit = RateLimit(requests_per_minute=50, retries=5)
    supports_search = True
    config_fields = [
        {"key": "access_token", "label": "Access token (app đã verify, quyền ads_read)", "secret": True, "required": True},
        {"key": "api_version", "label": "API version (mặc định v21.0)"},
        {"key": "countries", "label": "Quốc gia cho competitor tracker (VD FR,DE; cần nằm trong phạm vi API cấp quyền)"},
    ]

    def fetch_ads(self, params: FetchParams) -> Iterator[AdRecord]:
        self.authenticate()
        url = f"https://graph.facebook.com/{self.config.get('api_version', 'v21.0')}/ads_archive"
        q = {
            "access_token": self.config["access_token"], "search_terms": params.query or "",
            "ad_reached_countries": "[" + ",".join(f'"{c}"' for c in params.countries if c != "ALL") + "]",
            "ad_active_status": "ACTIVE" if params.active_only else "ALL", "ad_type": "ALL", "limit": min(params.limit, 250),
            "fields": "id,page_id,page_name,ad_creative_bodies,ad_creative_link_titles,ad_creative_link_captions,"
                      "ad_delivery_start_time,ad_delivery_stop_time,ad_snapshot_url,publisher_platforms,languages",
        }
        if params.page_ids:
            q["search_page_ids"] = ",".join(params.page_ids)
        n = 0
        while url and n < params.limit:
            data = self.request("GET", url, params=q).json()
            for a in data.get("data", []):
                n += 1
                yield AdRecord(
                    source=self.key, source_ad_id=a["id"], platform=(a.get("publisher_platforms") or ["facebook"])[0],
                    country=params.countries[0] if params.countries else None, page_id=a.get("page_id"),
                    advertiser=a.get("page_name"), ad_text=(a.get("ad_creative_bodies") or [None])[0],
                    title=(a.get("ad_creative_link_titles") or [None])[0], product_name=(a.get("ad_creative_link_titles") or [None])[0],
                    first_seen=a.get("ad_delivery_start_time"), last_seen=a.get("ad_delivery_stop_time"),
                    active=not a.get("ad_delivery_stop_time"), snapshot_url=a.get("ad_snapshot_url"), raw_source=a,
                )  # official API returns no media URLs — creatives come from the snapshot page / other sources
            url, q = (data.get("paging") or {}).get("next"), None


class ApifyMetaConnector(BaseConnector):
    key = "apify_meta"
    name = "Apify — Facebook Ads Library scraper"
    kind = "commercial_api"
    group = "ad_intel"
    rate_limit = RateLimit(requests_per_minute=30, retries=3)
    supports_search = True
    config_fields = [
        {"key": "token", "label": "APIFY_TOKEN", "secret": True, "required": True},
        {"key": "actor", "label": "Actor id (mặc định curious_coder~facebook-ads-library-scraper)"},
    ]

    def fetch_ads(self, params: FetchParams) -> Iterator[AdRecord]:
        self.authenticate()
        actor = (self.config.get("actor") or "curious_coder~facebook-ads-library-scraper").replace("/", "~")
        for country in params.countries or ["ALL"]:
            lib = ("https://www.facebook.com/ads/library/?active_status=" + ("active" if params.active_only else "all")
                   + f"&ad_type=all&country={country}&media_type={params.media_type or 'all'}")
            lib += (f"&search_type=page&view_all_page_id={params.page_ids[0]}" if params.page_ids
                    else f"&q={params.query or ''}&search_type=keyword_unordered")
            r = self.request(
                "POST", f"https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items",
                params={"token": self.config["token"], "timeout": 280},
                json={"urls": [{"url": lib}], "limitPerSource": params.limit, "scrapeAdDetails": False}, timeout=300,
            )
            for item in r.json():
                yield map_library_ad(item, self.key, country)
