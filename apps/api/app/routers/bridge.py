"""Browser Bridge: POST /api/imports/allegro.

Records come from the user-triggered Chrome extension (extensions/allegro-bridge), which reads ONLY the Allegro listing
page the user opened themselves. Auth: header X-ToolSpy-Token == TOOLSPY_BRIDGE_TOKEN (.env), constant-time compare.
AdminGuard in main.py exempts this exact path because the route authenticates itself.
Merges into data/imports/allegro.json (read by eu_market.allegro_import), dedup by offer_id, newer fetched_at wins, then
upserts exactly the accepted records into eu_listings (allegro_import.map_item + service.upsert_items, identity
(allegro, offer_id, PL), query = keyword) in the same request.
Optional job_id (an EUBotJob created by POST /api/eu-market/allegro/search provider=bridge) + status ok|blocked|captcha|error:
the job ends done | empty | blocked | captcha | failed. Unknown job → 404, foreign / already final (incl. timed out) job → 409;
records are saved (file + eu_listings) BEFORE the job check, so a late or wrong job_id never loses real data — the 4xx
detail carries the import stats.
"""
from __future__ import annotations

import hmac
import json
import os
import re
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_db

router = APIRouter(tags=["imports"])

DATA_PATH = Path(__file__).resolve().parents[2] / "data" / "imports" / "allegro.json"
MAX_RECORDS = 500
_LOCK = threading.Lock()
_OFFER_URL = re.compile(r"^https://allegro\.pl/(oferta|events/clicks)")


class BridgeIn(BaseModel):
    records: list[dict[str, Any]]
    page_url: Optional[str] = None
    parser_log: Optional[dict[str, Any]] = None
    job_id: Optional[str] = Field(None, max_length=32)
    keyword: Optional[str] = Field(None, max_length=200)
    status: Literal["ok", "blocked", "captcha", "error"] = "ok"  # blocked / captcha / error may come with zero records
    error: Optional[str] = Field(None, max_length=2000)


def _check_token(got: str | None) -> None:
    want = os.getenv("TOOLSPY_BRIDGE_TOKEN", "")
    if not want:
        raise HTTPException(503, "TOOLSPY_BRIDGE_TOKEN not configured")
    if not got or not hmac.compare_digest(got.encode(), want.encode()):
        raise HTTPException(401, "invalid bridge token (header X-ToolSpy-Token)")


def _parse_ts(v: Any) -> datetime | None:
    if not isinstance(v, str) or not v:
        return None
    try:
        d = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _num(v: Any) -> float | None:
    if v is None or v == "" or isinstance(v, bool):
        return None
    try:
        f = float(str(v).replace("\u00a0", "").replace(" ", "").replace(",", "."))
    except ValueError:
        return None
    return f if f >= 0 else None


def normalize(r: dict[str, Any]) -> tuple[dict | None, str | None]:
    """-> (clean record, None) or (None, reason)."""
    oid = str(r.get("offer_id") or "").strip()
    title = str(r.get("title") or "").strip()
    url = str(r.get("product_url") or "").strip()
    missing = [k for k, v in (("offer_id", oid), ("title", title), ("product_url", url)) if not v]
    if missing:
        return None, "missing " + ",".join(missing)
    if not oid.isdigit():
        return None, "offer_id must be numeric"
    if not _OFFER_URL.match(url):
        return None, "product_url must be an https://allegro.pl/ offer URL"
    ts = _parse_ts(r.get("fetched_at")) or datetime.now(timezone.utc)
    img = r.get("image_url")
    img = img if isinstance(img, str) and img.startswith("https://") else None
    return {
        "offer_id": oid, "title": title[:500], "price": _num(r.get("price")),
        "currency": (str(r.get("currency") or "PLN").strip().upper() or "PLN")[:3],
        "image_url": img, "product_url": url,
        "seller": (str(r["seller"]).strip()[:200] or None) if r.get("seller") else None,
        "sponsored": bool(r.get("sponsored")), "url_unresolved": bool(r.get("url_unresolved")),
        "source": "allegro", "fetched_at": ts.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "via": "browser_bridge",
    }, None


def _load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8") or "[]")
    if not isinstance(data, list):
        raise HTTPException(500, f"{path.name} is not a JSON list; refusing to overwrite")
    return data


def _atomic_write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def merge(path: Path, records: list[dict]) -> dict:
    with _LOCK:
        rows = _load(path)
        index = {str(r.get("offer_id")): i for i, r in enumerate(rows) if isinstance(r, dict) and r.get("offer_id")}
        added = updated = unchanged = 0
        for rec in records:
            i = index.get(rec["offer_id"])
            if i is None:
                index[rec["offer_id"]] = len(rows)
                rows.append(rec)
                added += 1
                continue
            old_ts = _parse_ts(rows[i].get("fetched_at"))
            if old_ts is None or _parse_ts(rec["fetched_at"]) >= old_ts:
                rows[i] = {**rows[i], **rec}  # keep any extra fields other importers stored
                updated += 1
            else:
                unchanged += 1
        if added or updated:
            _atomic_write(path, rows)
        return {"added": added, "updated": updated, "unchanged": unchanged, "total": len(rows)}


def upsert_listings(db: Session, records: list[dict], query: str | None) -> list[int]:
    """Normalized bridge records -> eu_listings. Click-tracking URLs are not stored: the offer URL is rebuilt from offer_id."""
    from ..eu_market.allegro_import import map_item
    from ..eu_market.service import upsert_items

    items = []
    for rec in records:
        it = map_item({**rec, "product_url": None if rec["url_unresolved"] or "/oferta/" not in rec["product_url"] else rec["product_url"]})
        if it:
            it["data_access"] = "browser_bridge"
            it["raw"] = {"import": "browser_bridge", "row": rec}
            items.append(it)
    return upsert_items(db, items, query=query) if items else []


@router.post("/api/imports/allegro")
def import_allegro(body: BridgeIn, x_toolspy_token: str | None = Header(None), db: Session = Depends(get_db)):
    _check_token(x_toolspy_token)
    if len(body.records) > MAX_RECORDS:
        raise HTTPException(413, f"max {MAX_RECORDS} records per request")
    clean: dict[str, dict] = {}
    rejected = []
    for n, r in enumerate(body.records):
        rec, why = normalize(r) if isinstance(r, dict) else (None, "not an object")
        if rec is None:
            rejected.append({"index": n, "reason": why})
        elif rec["offer_id"] not in clean or rec["fetched_at"] >= clean[rec["offer_id"]]["fetched_at"]:
            clean[rec["offer_id"]] = rec
    stats = merge(DATA_PATH, list(clean.values())) if clean else {"added": 0, "updated": 0, "unchanged": 0, "total": len(_load(DATA_PATH))}
    from ..eu_market import allegro_bot
    from ..eu_market.models import EUBotJob

    j = db.get(EUBotJob, body.job_id) if body.job_id else None
    keyword = " ".join((body.keyword or (j.keyword if j else "") or "").split())[:200] or None
    ids = upsert_listings(db, list(clean.values()), keyword)
    out = {"received": len(body.records), "accepted": len(clean), "rejected": rejected, **stats, "written": len(ids)}
    if not body.job_id:
        return out
    allegro_bot.expire(db)
    if j is None:
        raise HTTPException(404, {"code": "job_not_found", "message": "job không tồn tại — bản ghi vẫn đã được lưu", "import": out})
    db.refresh(j)
    if j.source != "allegro" or j.provider != "bridge" or j.status not in allegro_bot.LIVE:
        raise HTTPException(409, {"code": "job_not_open", "message": f"job {j.provider}/{j.status} không nhận kết quả bridge — bản ghi vẫn đã được lưu",
                                  "status": j.status, "import": out})
    return {**out, "job": allegro_bot.finish_bridge(db, j, body.status, len(body.records), ids, body.error)}
