"""Generic adapters: export folders (Pipiads/Minea/BigSpy…), any REST API with field mapping,
any Apify actor (TikTok, Pipiads…), TikTok Commercial Content API (official)."""
from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Iterator

from ...storage import DATA_DIR
from ..base import BaseConnector, FetchParams, NotConfigured, RateLimit
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
    "days_running": ["daysrunning", "days", "runningdays", "duration"],
}


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
        source=source, source_ad_id=str(ad_id), platform=(get("platform") or "").lower() or None, country=country,
        page_id=get("page_id"), advertiser=get("advertiser"), ad_text=get("ad_text"), title=get("title"),
        cta_text=get("cta_text"), creative_type="video" if get("video") else ("image" if get("image") else None),
        media=media, landing_page=get("landing_page"), product_name=get("title"), product_url=get("landing_page"),
        price=_num(get("price")), currency=get("currency"), first_seen=str(first) if first else None,
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


class TikTokCommercialConnector(BaseConnector):
    """TikTok Commercial Content API (official; ads shown in EU/EEA/UK/CH). Needs approved research access."""

    key = "tiktok_commercial"
    name = "TikTok Commercial Content API (official)"
    kind = "official_api"
    group = "transparency"
    rate_limit = RateLimit(requests_per_minute=30, retries=3)
    supports_search = True
    config_fields = [
        {"key": "client_key", "label": "Client key", "secret": True, "required": True},
        {"key": "client_secret", "label": "Client secret", "secret": True, "required": True},
    ]

    def _token(self) -> str:
        r = self.request("POST", "https://open.tiktokapis.com/v2/oauth/token/",
                         data={"client_key": self.config["client_key"], "client_secret": self.config["client_secret"],
                               "grant_type": "client_credentials"})
        return r.json()["access_token"]

    def fetch_ads(self, params: FetchParams) -> Iterator[AdRecord]:
        self.authenticate()
        tok = self._token()
        today = datetime.utcnow().strftime("%Y%m%d")
        body = {"filters": {"ad_published_date_range": {"min": params.since.replace("-", "") if params.since else "20240101", "max": today},
                            "country_code": (params.countries or ["ALL"])[0]},
                "search_term": params.query or "", "max_count": min(params.limit, 50)}
        r = self.request("POST", "https://open.tiktokapis.com/v2/research/adlib/ad/query/?fields=ad,advertiser",
                         headers={"Authorization": f"Bearer {tok}"}, json=body)
        for it in (r.json().get("data") or {}).get("ads", []):
            ad, adv = it.get("ad", {}), it.get("advertiser", {})
            vids = ad.get("videos") or []
            yield AdRecord(
                source=self.key, source_ad_id=str(ad.get("id")), platform="tiktok", country=body["filters"]["country_code"],
                advertiser=adv.get("business_name"), page_id=str(adv.get("business_id") or ""),
                first_seen=ad.get("first_shown_date"), last_seen=ad.get("last_shown_date"), active=ad.get("status") == "active",
                creative_type="video" if vids else ("image" if ad.get("image_urls") else None),
                media=[MediaItem("video", v["url"], preview_url=v.get("cover_image_url")) for v in vids if v.get("url")]
                + [MediaItem("image", u) for u in ad.get("image_urls") or []],
                raw_source=it,
            )
