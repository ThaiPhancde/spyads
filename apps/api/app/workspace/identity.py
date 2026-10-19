"""Identity is source + platform-issued ID, never advertiser display name."""
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from ..models import Advertiser


def resolve(db, rec):
    # A source without an advertiser ID remains an explicitly unverified ad-scoped identity.
    # This preserves Follow without accidentally merging independent advertisers by display name.
    external = str(rec.page_id).strip() if rec.page_id else f"unverified:ad:{rec.source_ad_id}"
    advertiser = resolve_values(db, rec.source, external, rec.advertiser or external,
                                rec.platform, rec.country, rec.advertiser_url)
    if rec.advertiser:
        advertiser.name = rec.advertiser
    if rec.advertiser_url:
        advertiser.page_url = rec.advertiser_url
    raw = rec.raw_source or {}
    raw = {**raw, **(raw.get("snapshot") or {})} if isinstance(raw.get("snapshot"), dict) else raw
    avatar = raw.get("page_profile_picture_url") or raw.get("avatar_url")
    if isinstance(avatar, str) and avatar.startswith("https://"):
        advertiser.avatar_url = avatar
    return advertiser


def resolve_values(db, source, external, name, platform=None, country=None, url=None):
    insert = sqlite_insert if db.bind.dialect.name == "sqlite" else pg_insert
    stmt = insert(Advertiser).values(source=source, source_advertiser_id=external, name=name,
                                     platform=platform, country=country, page_url=url, is_competitor_watched=False)
    db.execute(stmt.on_conflict_do_nothing(index_elements=["source", "source_advertiser_id"]))
    return db.scalar(select(Advertiser).where(Advertiser.source == source, Advertiser.source_advertiser_id == external))
