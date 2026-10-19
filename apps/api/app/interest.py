"""Soft deduplication (account policy 2026-10-19): other MKTs' Save / Favorite on a Product Cluster.

Interest is a hint, not a lock: nothing is hidden or reserved. It lowers the *personal* ranking for everybody else
(Favorite more than Saved) and shows the label "Sản phẩm đã có người quan tâm". It is counted per person per cluster
root — three saved ads of one product by one MKT are one signal — and read live from `mkt_ad_interactions`, so
Unsave / Unfavorite take effect on the next read. Identities, notes and counts of other MKTs are never returned.
"""
import os
import time
from datetime import datetime

from sqlalchemy import DateTime, String, or_, select
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base, SessionLocal
from .models import Ad


class AppSetting(Base):
    """Tiny key/value table so tuning knobs can be changed with one SQL statement (no restart, no account role)."""
    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(200))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


_SET = {"at": 0.0, "v": {}}


def setting(key, default):
    """DB value (read at most every 30 s) → env var → default."""
    if time.time() - _SET["at"] > 30:
        try:
            with SessionLocal() as db:
                _SET.update(at=time.time(), v={k: v for k, v in db.execute(select(AppSetting.key, AppSetting.value))})
        except Exception:  # noqa: BLE001 — table not created yet: fall back to env
            _SET.update(at=time.time(), v={})
    return float(_SET["v"].get(key, os.getenv(key, default)))

LABEL = "Sản phẩm đã có người quan tâm"


def weights():
    return (setting("RECO_INTEREST_PENALTY_SAVED", "0.15"), setting("RECO_INTEREST_PENALTY_FAVORITE", "0.30"),
            setting("RECO_INTEREST_PENALTY_PER_EXTRA", "0.05"), setting("RECO_INTEREST_PENALTY_MAX", "0.5"))


def others(db, uid):
    """root product id → {"saved": {user ids}, "favorite": {user ids}} for every MKT except `uid`."""
    from .recommend import redirects, root
    from .workspace.models import AdInteraction
    red = redirects(db)
    out = {}
    q = (select(AdInteraction.user_id, Ad.product_id, AdInteraction.saved, AdInteraction.favorite)
         .join(Ad, Ad.id == AdInteraction.ad_id)
         .where(Ad.product_id.is_not(None), or_(AdInteraction.saved.is_(True), AdInteraction.favorite.is_(True))))
    if uid is not None:
        q = q.where(AdInteraction.user_id != uid)
    for user, pid, saved, fav in db.execute(q):
        e = out.setdefault(root(red, pid), {"saved": set(), "favorite": set()})
        if fav:
            e["favorite"].add(user)
        elif saved:
            e["saved"].add(user)
    for e in out.values():
        e["saved"] -= e["favorite"]  # one person counts once, at their strongest level
    return out


def penalty(e):
    if not e:
        return 0.0
    w_saved, w_fav, per_extra, cap = weights()
    people = len(e["saved"]) + len(e["favorite"])
    if not people:
        return 0.0
    base = w_fav if e["favorite"] else w_saved
    return min(cap, base + per_extra * (people - 1))


def label(e):
    """What another MKT may see: that someone is interested, and how strongly — never who or how many."""
    if not e or not (e["saved"] or e["favorite"]):
        return None
    return {"label": LABEL, "level": "favorite" if e["favorite"] else "saved"}


def apply(fit, e):
    """Penalised copy of a fit dict {score, reasons, …}; the raw score stays in `score_before_interest`."""
    p = penalty(e)
    if not fit or not p:
        return fit
    reasons = list(fit.get("reasons") or [])
    reasons.append("Đã có MKT khác quan tâm — giảm ưu tiên để tránh trùng sản phẩm")
    return {**fit, "score": round(fit["score"] * (1 - p), 1), "score_before_interest": fit["score"],
            "interest_penalty": round(p, 3), "reasons": reasons}
