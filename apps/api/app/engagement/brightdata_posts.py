"""Bright Data Web Scraper API — Facebook Posts / TikTok Posts (https://docs.brightdata.com/datasets/scrapers).

Dataset ids and the output field names below were read from this account on 2026-10-14 (GET /datasets/list, GET /datasets/<id>/metadata):
  TikTok - Posts             gd_lu702nij2f790tmv9h   post_id · digg_count · comment_count · share_count · play_count · collect_count · create_time
  Facebook - Posts by URL    gd_lyclm1571iy3mv57zw   post_id · likes · num_comments · num_shares · video_view_count · play_count · date_posted
Synchronous endpoint POST /datasets/v3/scrape?dataset_id=…&format=json with {"input": [{"url": …}]} (≤ 1 min, else 202 + snapshot_id:
progress / snapshot are polled, billed once). Billed per delivered record, so it is a PAID product — never called unless
BRIGHTDATA_STRUCTURED_ENABLED is on, and then only inside its own budget (BRIGHTDATA_POSTS_MONTHLY_LIMIT, default 30; DAILY default 10),
reserved on the shared ledger (provider "brightdata_posts") before the request leaves. A field the record does not carry is None, not 0.
"""
from __future__ import annotations

import json
import os
import re
import time

import httpx

from ..pricing import quota

PROVIDER = "brightdata_posts"
API = "https://api.brightdata.com/datasets/v3"
DATASETS = {"tiktok": os.getenv("BRIGHTDATA_TIKTOK_POSTS_DATASET", "gd_lu702nij2f790tmv9h").split("#")[0].strip(),
            "facebook": os.getenv("BRIGHTDATA_FACEBOOK_POSTS_DATASET", "gd_lyclm1571iy3mv57zw").split("#")[0].strip(),
            # "Facebook - Pages Posts by Profile URL": the recent posts of a page (input {"url", "num_of_posts"}); the pool ads are matched against
            "facebook_page": os.getenv("BRIGHTDATA_FACEBOOK_PAGE_POSTS_DATASET", "gd_lkaxegm826bjpoo9m5").split("#")[0].strip()}
# our metric → the dataset's field names, first present one wins (a present 0 counts; a missing / null field does not)
FIELDS = {
    "tiktok": {"likes": ("digg_count",), "comments": ("comment_count",), "shares": ("share_count", "num_share_count"), "views": ("play_count",)},
    "facebook": {"likes": ("likes",), "comments": ("num_comments",), "shares": ("num_shares",), "views": ("video_view_count", "play_count")},
}
_UNIT = {"k": 1e3, "m": 1e6, "b": 1e9}
_blocked = {"until": 0.0}  # Bright Data said the account cannot use the Web Scraper API (400 "Customer is not active"): stop asking for a while


class PostsError(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code, self.detail = code, detail


def _flag(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).split("#")[0].strip().lower() in ("1", "true", "yes", "on")


def token() -> str | None:
    return (os.getenv("BRIGHTDATA_API_TOKEN") or "").strip() or None


def unavailable() -> str | None:
    if not _flag("BRIGHTDATA_ENABLED"):
        return "brightdata_disabled"
    if not token():
        return "missing_api_token"
    if not _flag("BRIGHTDATA_STRUCTURED_ENABLED"):
        return "structured_disabled"  # Web Scraper API datasets are billed per record: opt-in
    if quota.env_int("BRIGHTDATA_POSTS_MONTHLY_LIMIT", 30) <= 0:
        return "posts_budget_zero"
    if _blocked["until"] > time.time():
        return "account_not_active"
    return None


def to_int(v) -> int | None:
    """Provider number → int. None for missing / null / unparsable; "1.2K" → 1200; 0 stays 0 (a real zero)."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return int(v) if v == v and v not in (float("inf"), float("-inf")) and v >= 0 else None
    if isinstance(v, str):
        s = v.strip().lower().replace(",", "").replace(" ", "")
        m = re.fullmatch(r"(\d+(?:\.\d+)?)([kmb]?)", s)
        if m:
            return int(round(float(m.group(1)) * _UNIT.get(m.group(2), 1)))
    return None


def parse(platform: str, rec: dict) -> dict:
    """{likes, comments, shares, views, post_id, fields: {metric: field read}} — only what the record really carries."""
    out: dict = {"post_id": str(rec["post_id"]) if rec.get("post_id") not in (None, "") else None, "fields": {}}
    for metric, names in FIELDS[platform].items():
        out[metric] = None
        for n in names:
            v = to_int(rec.get(n))
            if v is not None:
                out[metric], out["fields"][metric] = v, n
                break
    return out


def _records(r: httpx.Response) -> list[dict]:
    text = r.text.strip()
    if not text:
        return []
    try:
        j = json.loads(text)
    except ValueError:  # ndjson
        j = [json.loads(line) for line in text.splitlines() if line.strip()]
    if isinstance(j, dict):
        j = j.get("data") or j.get("records") or [j]
    return [x for x in j if isinstance(x, dict) and not x.get("error")]


class PostsPending(Exception):
    """The collection did not finish inside the wait: keep the snapshot id, collect() it later (progress / snapshot are free)."""

    def __init__(self, snapshot_id: str):
        super().__init__(f"snapshot {snapshot_id} not ready")
        self.snapshot_id = snapshot_id


def submit(dataset_key: str, inputs: list[dict], wait_s: float = 40, timeout: float = 75) -> list[dict]:
    """POST /datasets/v3/scrape for `inputs` (one request on the posts ledger). Records, or PostsPending(snapshot_id) when the sync window
    passes, or PostsError. A rejected request (400 / 401 / 403 / 422) is never billed or counted."""
    why = unavailable()
    if why:
        raise PostsError(why)
    dataset = DATASETS.get(dataset_key)
    if not dataset:
        raise PostsError("unsupported_platform", dataset_key)
    try:
        cid = quota.reserve(PROVIDER, "scrape")
    except quota.QuotaError as e:
        raise PostsError(e.code, e.detail)
    t0 = time.time()
    h = {"Authorization": f"Bearer {token()}", "Content-Type": "application/json"}
    try:
        with httpx.Client(timeout=timeout) as c:
            r = c.post(f"{API}/scrape", params={"dataset_id": dataset, "format": "json"}, headers=h, json={"input": inputs})
            if r.status_code == 202:
                sid = r.json().get("snapshot_id")
                for _ in range(max(1, int(wait_s // 3))):
                    time.sleep(3)
                    st = c.get(f"{API}/progress/{sid}", headers=h).json().get("status")
                    if st == "ready":
                        r = c.get(f"{API}/snapshot/{sid}", params={"format": "json"}, headers=h)
                        break
                    if st == "failed":
                        quota.finish(cid, False, 202, t0, "snapshot_failed")
                        raise PostsError("snapshot_failed", str(sid))
                else:
                    quota.finish(cid, True, 202, t0, None, counted=True)  # accepted: it will be billed per record when it finishes
                    raise PostsPending(sid)
    except httpx.ConnectError as e:
        quota.finish(cid, False, None, t0, "network_error", counted=False)
        raise PostsError("network_error", str(e)[:120])
    except httpx.TimeoutException:
        quota.finish(cid, False, None, t0, "timeout")
        raise PostsError("timeout", "Bright Data did not answer in time")
    if r.status_code != 200:
        code = {401: "unauthorized", 403: "forbidden", 429: "rate_limited", 400: "bad_request"}.get(r.status_code, "provider_error")
        if r.status_code == 400 and "not active" in r.text.lower():  # measured 2026-10-14: before the account was activated
            code = "account_not_active"
            _blocked["until"] = time.time() + 6 * 3600
        quota.finish(cid, False, r.status_code, t0, code, counted=False if r.status_code in (400, 401, 403, 422) else None)
        raise PostsError(code, f"HTTP {r.status_code}: {r.text[:160]}")
    recs = _records(r)
    quota.finish(cid, bool(recs), 200, t0, None if recs else "no_record", counted=True if recs else False)
    return recs


def collect(snapshot_id: str) -> tuple[str, list[dict]]:
    """Free: ('ready', records) | ('running', []) | ('failed', [])."""
    h = {"Authorization": f"Bearer {token()}"}
    with httpx.Client(timeout=60) as c:
        st = c.get(f"{API}/progress/{snapshot_id}", headers=h).json().get("status")
        if st == "ready":
            return "ready", _records(c.get(f"{API}/snapshot/{snapshot_id}", params={"format": "json"}, headers=h))
    return ("failed" if st == "failed" else "running"), []


def fetch(platform: str, url: str, timeout: float = 75) -> dict:
    """One post URL → the raw record (a dict) or PostsError / PostsPending."""
    recs = submit(platform, [{"url": url}], timeout=timeout)
    if not recs:
        raise PostsError("not_found", "the dataset returned no record for this URL (private / deleted post?)")
    return recs[0]


def parse_page_post(rec: dict) -> dict:
    """A Facebook page-post record → the fields matching + the card need (None when absent)."""
    imgs = [a.get("thumbnail_url") or a.get("url") for a in (rec.get("attachments") or []) if isinstance(a, dict)]
    imgs = [u for u in [rec.get("post_image"), rec.get("post_external_image"), *imgs] if isinstance(u, str) and u.startswith("http")]
    nums = parse("facebook", rec)
    return {"post_id": nums["post_id"], "url": rec.get("url"), "content": rec.get("content"), "external_link": rec.get("post_external_link"),
            "image_urls": list(dict.fromkeys(imgs))[:4], "is_sponsored": rec.get("is_sponsored") if isinstance(rec.get("is_sponsored"), bool) else None,
            "posted_at": rec.get("date_posted"), "likes": nums["likes"], "comments": nums["comments"], "shares": nums["shares"], "views": nums["views"],
            "page_key": str(rec.get("profile_id") or rec.get("delegate_page_id") or "") or None, "fields": nums["fields"]}
