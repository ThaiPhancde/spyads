from sqlalchemy import and_, or_, select, true

from ..models import Ad
from .models import AdvertiserFilter, Follow, Profile


def has_ad_filter(db):
    uid = db.info.get("user_id")
    if not uid:
        return False
    p = db.get(Profile, uid)
    return bool((p and p.advertiser_mode != "all") or db.scalar(select(AdvertiserFilter.advertiser_id)
        .where(AdvertiserFilter.user_id == uid, AdvertiserFilter.mode == "blacklist").limit(1)))


def ad_filter(db):
    uid = db.info.get("user_id")
    if not uid:
        return true()
    profile = db.get(Profile, uid)
    mode = profile.advertiser_mode if profile else "all"
    blocked = select(AdvertiserFilter.advertiser_id).where(AdvertiserFilter.user_id == uid, AdvertiserFilter.mode == "blacklist")
    condition = or_(Ad.advertiser_id.is_(None), Ad.advertiser_id.not_in(blocked))
    if mode == "followed":
        condition = and_(condition, Ad.advertiser_id.in_(select(Follow.advertiser_id).where(Follow.user_id == uid)))
    elif mode == "whitelist":
        condition = and_(condition, Ad.advertiser_id.in_(select(AdvertiserFilter.advertiser_id).where(
            AdvertiserFilter.user_id == uid, AdvertiserFilter.mode == "whitelist")))
    return condition
