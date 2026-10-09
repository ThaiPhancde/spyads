"""Generic adapters: export folders (Pipiads/Minea/BigSpy…), any REST API with field mapping,
any Apify actor (TikTok, Pipiads…), TikTok Commercial Content API (official)."""
from __future__ import annotations

import csv
import io
import json
import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator

import httpx

from ...storage import DATA_DIR
from ..base import AuthExpired, BaseConnector, ConnectorError, FetchParams, NotConfigured, RateLimit
from ..contract import AdRecord, MediaItem

# Header aliases seen in spy-tool exports (lower-cased, spaces/underscores ignored).
ALIASES = {
    "source_ad_id": ["adid", "id", "adarchiveid", "videoid", "postid", "adlibraryid"],
    "advertiser": ["advertiser", "pagename", "shopname", "store", "brand", "author", "nickname", "page"],
    "page_id": ["pageid"],
    "ad_text": ["adtext", "caption", "description", "text", "primarytext", "body", "adcopy", "desc"],
    "title": ["title", "headline", "productname", "product"],
    "cta_text": ["cta", "calltoaction", "button", "buttontext"],
    "landing_page": ["landingpage", "landingurl", "url", "link", "storeurl", "producturl", "website", "shopurl"],
    "video": ["videourl", "video", "mediaurl", "videolink", "creativeurl", "playurl"],
    "image": ["imageurl", "image", "thumbnail", "cover", "coverurl", "thumbnailurl"],
    "country": ["country", "region", "countries", "market", "geo"],
    "platform": ["platform", "network"],
    "first_seen": ["firstseen", "startdate", "createdat", "createtime", "publishdate", "launchdate", "firstshown"],
    "last_seen": ["lastseen", "enddate", "updatedat", "lastshown"],
    "likes": ["likes", "likecount", "diggcount", "reactions"],
    "comments": ["comments", "commentcount"],
    "shares": ["shares", "sharecount"],
    "views": ["views", "playcount", "impressions", "viewcount"],
    "price": ["price", "productprice"],
    "currency": ["currency"],
    "sold": ["sold", "soldcount", "sales", "salescount", "monthsold", "monthlysales", "tradecount"],
    "days_running": ["daysrunning", "days", "runningdays", "duration"],
}

# exports / APIs without a platform column: the tool tells us the network (else ingest would default to facebook)
SOURCE_PLATFORM = {"pipiads": "tiktok", "tiktok": "tiktok", "tiktok_cc": "tiktok", "minea": "facebook", "bigspy": "facebook",
                   "foreplay": "facebook", "adspy": "facebook", "1688": "1688", "taobao": "taobao"}


def _norm(h: str) -> str:
    return re.sub(r"[\s_\-()]+", "", h.lower())


def _num(v):
    if v in (None, ""):
        return None
    s = str(v).replace(",", "").strip().lower()
    mult = 1
    if s.endswith("k"):
        mult, s = 1_000, s[:-1]
    elif s.endswith("m"):
        mult, s = 1_000_000, s[:-1]
    try:
        return float(s) * mult
    except ValueError:
        return None


def row_to_record(row: dict, source: str, default_country: str | None = None, mapping: dict | None = None) -> AdRecord | None:
    nrow = {_norm(k): v for k, v in row.items() if k}
    def get(field):
        if mapping and mapping.get(field):
            return row.get(mapping[field])
        for a in ALIASES.get(field, []):
            if nrow.get(a) not in (None, ""):
                return nrow[a]
        return None
    ad_id = get("source_ad_id") or get("video") or get("landing_page")
    if not ad_id:
        return None
    media = []
    if get("video"):
        media.append(MediaItem("video", str(get("video")), preview_url=get("image")))
    elif get("image"):
        media.append(MediaItem("image", str(get("image"))))
    country = (get("country") or default_country or "")
    country = str(country).split(",")[0].strip().upper()[:2] or None
    first = get("first_seen")
    if not first and _num(get("days_running")):
        from datetime import timedelta
        first = (datetime.utcnow() - timedelta(days=_num(get("days_running")))).date().isoformat()
    return AdRecord(
        source=source, source_ad_id=str(ad_id), platform=(get("platform") or "").lower() or SOURCE_PLATFORM.get(source),
        country=country,
        page_id=get("page_id"), advertiser=get("advertiser"), ad_text=get("ad_text"), title=get("title"),
        cta_text=get("cta_text"), creative_type="video" if get("video") else ("image" if get("image") else None),
        media=media, landing_page=get("landing_page"), product_name=get("title"), product_url=get("landing_page"),
        price=_num(get("price")), currency=get("currency"),
        sold_count=int(_num(get("sold"))) if _num(get("sold")) else None, first_seen=str(first) if first else None,
        last_seen=get("last_seen"), active=True,
        likes=_num(get("likes")), comments=_num(get("comments")), shares=_num(get("shares")), views=_num(get("views")),
        raw_source=row,
    )


def parse_export(content: bytes, filename: str) -> list[dict]:
    if filename.lower().endswith(".json"):
        data = json.loads(content)
        return data if isinstance(data, list) else data.get("data") or data.get("items") or []
    if filename.lower().endswith((".xlsx", ".xls")):
        try:
            import openpyxl
        except ImportError as e:
            raise NotConfigured("Cần `pip install openpyxl` để đọc XLSX") from e
        ws = openpyxl.load_workbook(io.BytesIO(content), read_only=True).active
        rows = list(ws.iter_rows(values_only=True))
        head = [str(h or "") for h in rows[0]]
        return [dict(zip(head, r)) for r in rows[1:]]
    return list(csv.DictReader(io.StringIO(content.decode("utf-8-sig", errors="replace"))))


class ExportFolderConnector(BaseConnector):
    """Watch folder /data/import/<source>/ — drop Pipiads / Minea / BigSpy exports there (or upload in UI)."""

    key = "export"
    name = "Export folder (Pipiads / Minea / BigSpy CSV-XLSX-JSON)"
    kind = "export"
    group = "ad_intel"
    rate_limit = RateLimit(requests_per_minute=600)
    config_fields = [
        {"key": "source", "label": "Tên nguồn (pipiads / minea / bigspy …)", "required": True},
        {"key": "country", "label": "Quốc gia mặc định nếu file không có"},
        {"key": "mapping", "label": "Mapping cột tuỳ chọn (JSON: {\"video\": \"Video URL\"})"},
    ]

    def folder(self) -> Path:
        return DATA_DIR / "import" / self.config.get("source", "export")

    def fetch_ads(self, params: FetchParams) -> Iterator[AdRecord]:
        self.authenticate()
        folder = self.folder()
        folder.mkdir(parents=True, exist_ok=True)
        done = folder / "done"
        done.mkdir(exist_ok=True)
        mapping = self.config.get("mapping")
        if isinstance(mapping, str) and mapping.strip():
            mapping = json.loads(mapping)
        for f in sorted(folder.iterdir()):
            if not f.is_file() or f.suffix.lower() not in (".csv", ".json", ".xlsx"):
                continue
            for row in parse_export(f.read_bytes(), f.name):
                rec = row_to_record(row, self.config["source"], self.config.get("country"), mapping or None)
                if rec:
                    yield rec
            f.rename(done / f"{datetime.utcnow():%Y%m%d%H%M%S}_{f.name}")


def _path(obj, path: str | None):
    if not path:
        return None
    for part in path.split("."):
        if obj is None:
            return None
        obj = obj[int(part)] if isinstance(obj, list) and part.isdigit() and int(part) < len(obj) else (obj.get(part) if isinstance(obj, dict) else None)
    return obj


class GenericHttpConnector(BaseConnector):
    """Any provider with an API key (Pipiads Enterprise, Minea, BigSpy, Foreplay…): url + items_path + mapping."""

    key = "http"
    name = "Generic REST API (mapping)"
    kind = "commercial_api"
    group = "ad_intel"
    rate_limit = RateLimit(requests_per_minute=20, retries=3)
    supports_search = True
    config_fields = [
        {"key": "source", "label": "Tên nguồn (pipiads, minea…)", "required": True},
        {"key": "url", "label": "Endpoint URL ({query} {country} sẽ được thay)", "required": True},
        {"key": "method", "label": "GET/POST"},
        {"key": "headers", "label": "Headers JSON (VD {\"Authorization\": \"Bearer …\"})", "secret": True},
        {"key": "body", "label": "Body JSON cho POST ({query} {country})"},
        {"key": "items_path", "label": "Đường dẫn tới list (VD data.list)"},
        {"key": "mapping", "label": "Mapping JSON field→path (source_ad_id, advertiser, ad_text, title, video, image, landing_page, first_seen, likes…)", "required": True},
    ]

    def fetch_ads(self, params: FetchParams) -> Iterator[AdRecord]:
        self.authenticate()
        cfg = self.config
        load = lambda v: json.loads(v) if isinstance(v, str) and v.strip() else (v or None)
        for country in params.countries or ["ALL"]:
            fill = lambda s: s.replace("{query}", params.query or "").replace("{country}", country) if isinstance(s, str) else s
            body = load(cfg.get("body"))
            if body:
                body = json.loads(fill(json.dumps(body)))
            r = self.request(cfg.get("method", "GET").upper(), fill(cfg["url"]), headers=load(cfg.get("headers")), json=body)
            items = _path(r.json(), cfg.get("items_path")) if cfg.get("items_path") else r.json()
            mapping = load(cfg["mapping"])
            for it in items or []:
                row = {k: _path(it, p) for k, p in mapping.items()}
                rec = row_to_record({k: v for k, v in row.items()}, cfg["source"], country if country != "ALL" else None,
                                    {k: k for k in row})
                if rec:
                    rec.raw_source = it
                    yield rec


class ApifyActorConnector(BaseConnector):
    """Any Apify actor (e.g. TikTok Creative Center / TikTok ads / Pipiads scrapers). Output mapped with aliases or `mapping`."""

    key = "apify_actor"
    name = "Apify actor (TikTok, Pipiads…)"
    kind = "commercial_api"
    group = "ad_intel"
    rate_limit = RateLimit(requests_per_minute=20, retries=2)
    supports_search = True
    config_fields = [
        {"key": "source", "label": "Tên nguồn (tiktok, pipiads…)", "required": True},
        {"key": "token", "label": "APIFY_TOKEN", "secret": True, "required": True},
        {"key": "actor", "label": "Actor id (vd: user~actor-name)", "required": True},
        {"key": "input", "label": "Input JSON ({query} {country} được thay)", "required": True},
        {"key": "mapping", "label": "Mapping JSON (tuỳ chọn)"},
    ]

    def fetch_ads(self, params: FetchParams) -> Iterator[AdRecord]:
        self.authenticate()
        cfg = self.config
        for country in params.countries or ["ALL"]:
            raw_in = cfg["input"] if isinstance(cfg["input"], str) else json.dumps(cfg["input"])
            inp = json.loads(raw_in.replace("{query}", params.query or "").replace("{country}", country))
            r = self.request("POST", f"https://api.apify.com/v2/acts/{cfg['actor'].replace('/', '~')}/run-sync-get-dataset-items",
                             params={"token": cfg["token"], "timeout": 280}, json=inp, timeout=300)
            mapping = json.loads(cfg["mapping"]) if isinstance(cfg.get("mapping"), str) and cfg["mapping"].strip() else cfg.get("mapping")
            for item in r.json()[: params.limit]:
                row = {k: _path(item, p) for k, p in mapping.items()} if mapping else _flatten(item)
                rec = row_to_record(row, cfg["source"], country if country != "ALL" else None, {k: k for k in row} if mapping else None)
                if rec:
                    rec.raw_source = item
                    yield rec


def _flatten(d: dict, prefix: str = "") -> dict:
    out = {}
    for k, v in d.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(_flatten(v, f"{key}_"))
        elif isinstance(v, list) and v and isinstance(v[0], (str, int, float)):
            out[key] = v[0]
        else:
            out[key] = v
    return out


# Commercial Content API only holds ads delivered in these countries (docs: commercial-content-api-supported-countries).
TIKTOK_CCA_COUNTRIES = {"AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IE", "IT", "LV", "LT",
                        "LU", "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE", "NO", "IS", "LI", "GB", "CH"}
TIKTOK_CCA_QUERY_FIELDS = ("ad.id,ad.first_shown_date,ad.last_shown_date,ad.status,ad.status_statement,ad.videos,ad.image_urls,"
                           "ad.reach,advertiser.business_id,advertiser.business_name,advertiser.paid_for_by")
TIKTOK_CCA_DETAIL_FIELDS = ("ad.id,ad.title,ad.external_url,ad.call_to_action,ad.advertising_objective,advertiser.country_code,"
                            "advertiser.follower_count,advertiser.profile_url,ad_group.targeting_info")
_tiktok_tokens: dict[str, tuple[str, float]] = {}  # client_key -> (access_token, expires_at)


def _tiktok_count(v) -> int | None:
    """Reach comes abbreviated ("11K", "1.2M", "100K+") — keep the lower bound as an int."""
    m = re.match(r"([\d.]+)\s*([KMB]?)", str(v or "").upper())
    return int(float(m.group(1)) * {"": 1, "K": 1e3, "M": 1e6, "B": 1e9}[m.group(2)]) if m else None


def _tiktok_date(v) -> str | None:
    v = str(v or "")
    return f"{v[:4]}-{v[4:6]}-{v[6:8]}" if len(v) == 8 and v.isdigit() else None


def map_tiktok_commercial(it: dict, det: dict, countries: list[str], query: str | None) -> AdRecord:
    ad, adv = {**it.get("ad", {}), **(det.get("ad") or {})}, {**it.get("advertiser", {}), **(det.get("advertiser") or {})}
    targeted = sorted(c.upper() for c in ((det.get("ad_group") or {}).get("targeting_info") or {}).get("country") or [])
    cs = targeted or countries
    vids, reach = ad.get("videos") or [], (ad.get("reach") or {}).get("unique_users_seen")
    return AdRecord(
        source="tiktok_commercial", source_ad_id=str(ad.get("id")), platform="tiktok", platforms=["tiktok"],
        country=cs[0] if len(cs) == 1 else None, countries=cs or ["ALL"],
        advertiser=adv.get("business_name"), page_id=str(adv.get("business_id") or "") or None, advertiser_url=adv.get("profile_url"),
        title=ad.get("title"), ad_text=ad.get("title"), cta_text=ad.get("call_to_action"),
        creative_type="video" if vids else ("image" if ad.get("image_urls") else None),
        media=[MediaItem("video", v["url"], preview_url=v.get("cover_image_url")) for v in vids if v.get("url")]
        + [MediaItem("image", u) for u in ad.get("image_urls") or []],
        landing_page=ad.get("external_url"), product_url=ad.get("external_url"),
        first_seen=_tiktok_date(ad.get("first_shown_date")), last_seen=_tiktok_date(ad.get("last_shown_date")),
        active=ad.get("status") == "active" if ad.get("status") else None,
        reach=_tiktok_count(reach), impressions_text=str(reach) if reach else None, page_likes=adv.get("follower_count"),
        matched_query=query, snapshot_url=f"https://library.tiktok.com/ads/detail/?ad_id={ad.get('id')}",
        raw_source={"query": it, "detail": det},
    )


class TikTokCommercialConnector(BaseConnector):
    """TikTok Commercial Content API (official) — same ads as library.tiktok.com, but stable instead of 429 scraping.

    Coverage = ads delivered in EU/EEA/UK/CH only: PH / SA / AE / US / VN return nothing (no fallback to "all",
    or EU ads leak into PH jobs). Query: max 10 ads/request, paged with search_id; detail = 1 request/ad, so only
    the first `details` ads get landing page / CTA / targeting. Token: client_credentials, ~2 h, cached per process.
    """

    key = "tiktok_commercial"
    name = "TikTok Commercial Content API (official · EU/UK)"
    kind = "official_api"
    group = "transparency"
    rate_limit = RateLimit(requests_per_minute=30, retries=3)
    supports_search = True
    config_fields = [
        {"key": "client_key", "label": "Client key (để trống = TIKTOK_CLIENT_KEY trong .env)", "secret": True},
        {"key": "client_secret", "label": "Client secret (để trống = TIKTOK_CLIENT_SECRET trong .env)", "secret": True},
        {"key": "details", "label": "Số ad lấy chi tiết (landing page, CTA, targeting) mỗi lần tìm — mặc định 10"},
        {"key": "days", "label": "Số ngày gần nhất (mặc định 90, tối đa 365)"},
    ]
    API = "https://open.tiktokapis.com/v2"

    def authenticate(self) -> None:
        self.ck = self.config.get("client_key") or os.getenv("TIKTOK_CLIENT_KEY")
        self.cs = self.config.get("client_secret") or os.getenv("TIKTOK_CLIENT_SECRET")
        if not (self.ck and self.cs):
            raise NotConfigured("Thiếu TIKTOK_CLIENT_KEY / TIKTOK_CLIENT_SECRET (.env hoặc cấu hình connector)")

    def _token(self) -> str:
        tok, exp = _tiktok_tokens.get(self.ck, ("", 0.0))
        if time.time() < exp:
            return tok
        j = self.request("POST", f"{self.API}/oauth/token/", data={
            "client_key": self.ck, "client_secret": self.cs, "grant_type": "client_credentials"}).json()
        if not j.get("access_token"):  # wrong key/secret: {"error": "invalid_client", ...}
            raise AuthExpired(f"TikTok OAuth: {j.get('error')} — {j.get('error_description')}")
        _tiktok_tokens[self.ck] = (j["access_token"], time.time() + int(j.get("expires_in") or 7200) - 300)
        return j["access_token"]

    def _post(self, path: str, fields: str, body: dict) -> dict:
        try:
            r = self.request("POST", f"{self.API}/research/adlib/{path}/", params={"fields": fields},
                             headers={"Authorization": f"Bearer {self._token()}"}, json=body)
        except httpx.HTTPStatusError as e:  # 400 carries the reason in error.message
            raise ConnectorError(f"TikTok {path} HTTP {e.response.status_code}: {e.response.text[:300]}") from None
        j = r.json()
        err = j.get("error") or {}
        if err.get("code") not in (None, "ok"):
            raise ConnectorError(f"TikTok {path}: {err.get('code')} {err.get('message')} (log_id {err.get('log_id')})")
        return j.get("data") or {}

    def query(self, query: str, countries: list[str], days: int, active_only: bool, video_only: bool, search_id: str = "") -> dict:
        now = datetime.utcnow()
        filters = {"ad_published_date_range": {"min": (now - timedelta(days=min(days, 365))).strftime("%Y%m%d"),
                                               "max": now.strftime("%Y%m%d")}}
        if countries:
            filters["country_code_list"] = countries
        if active_only:
            filters["ad_status"] = "ACTIVE"
        if video_only:
            filters["ad_type"] = "VIDEO"
        body = {"filters": filters, "search_term": query[:50], "search_type": "fuzzy_phrase", "max_count": 10}
        if search_id:
            body["search_id"] = search_id
        return self._post("ad/query", TIKTOK_CCA_QUERY_FIELDS, body)

    def fetch_ads(self, params: FetchParams) -> Iterator[AdRecord]:
        self.authenticate()
        q = (params.query or "").strip()
        if not q:
            return
        codes = [c.upper() for c in params.countries or [] if c.upper() != "ALL"]
        eu = [c for c in codes if c in TIKTOK_CCA_COUNTRIES]
        if codes and not eu:
            return  # PH / SA / US …: not covered by the API
        days, details_left = int(self.config.get("days") or 90), int(self.config.get("details") or 10)
        sid, got, seen = "", 0, set()
        while got < params.limit:
            data = self.query(q, eu, days, params.active_only, params.media_type == "video", sid)
            for it in data.get("ads") or []:
                aid = (it.get("ad") or {}).get("id")
                if not aid or aid in seen:
                    continue
                seen.add(aid)
                det = {}
                if details_left > 0:
                    details_left -= 1
                    try:
                        det = self._post("ad/detail", TIKTOK_CCA_DETAIL_FIELDS, {"ad_id": aid})
                    except ConnectorError:
                        pass  # the query row alone is still a valid ad
                got += 1
                yield map_tiktok_commercial(it, det, eu, q)
            sid = data.get("search_id") or ""
            if not data.get("has_more") or not data.get("ads") or not sid:
                break

    def health_check(self) -> dict:
        self.authenticate()
        n = len(self.query("shop", ["FR"], 30, False, False).get("ads") or [])
        return {"status": "healthy" if n else "degraded", "probe": "shop/FR", "ads": n}
