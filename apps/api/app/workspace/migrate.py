"""Idempotent additive migration. No Ads, products, or legacy advertiser rows are deleted."""
from sqlalchemy import or_, select, text

from ..db import SessionLocal, auto_migrate
from ..models import Ad, Advertiser
from .identity import resolve_values
from .models import WorkspaceMigration

VERSION = "20261015_accounts_competitors_v1"
SINGLE_ROLE_VERSION = "20261019_single_mkt_role_v1"
IDENTITY_VERSION = "20261015_advertiser_identity_v2"


def migrate():
    auto_migrate()
    with SessionLocal.begin() as db:
        if db.bind.dialect.name == "postgresql":
            db.execute(text("SELECT pg_advisory_xact_lock(727018)"))
        single_role(db)
        first_time = not db.get(WorkspaceMigration, VERSION)
        if not first_time and db.get(WorkspaceMigration, IDENTITY_VERSION):
            return
        # Lock ingest during identity reassignment. Existing primary keys remain for unambiguous groups;
        # split only when one legacy advertiser has multiple actual source/ID identities.
        if db.bind.dialect.name == "postgresql":
            db.execute(text("SELECT pg_advisory_xact_lock(727001)"))
        rows = db.execute(select(Ad, Advertiser).outerjoin(Advertiser, Ad.advertiser_id == Advertiser.id)
                          .where(Ad.page_id.is_not(None)).order_by(Ad.id)).all() if first_time else []
        identities = {}
        for ad, old in rows:
            key = (ad.source, str(ad.page_id).strip())
            if not key[1]:
                continue
            if key not in identities:
                name = old.name if old else str(ad.page_id)
                if old and not old.source and not old.source_advertiser_id:
                    old.source, old.source_advertiser_id = key
                    db.flush()
                    identities[key] = old.id
                else:
                    identities[key] = resolve_values(db, *key, name, ad.platform, ad.country,
                                                     old.page_url if old else None).id
            ad.advertiser_id = identities[key]
        if first_time:
            db.add(WorkspaceMigration(version=VERSION))
        db.flush()
        # Legacy name-based groups can contain rows with no page ID from a different provider.
        # Those rows must not inherit the verified identity of the first identified ad above.
        mismatched = db.execute(select(Ad, Advertiser).join(Advertiser, Ad.advertiser_id == Advertiser.id).where(
            Advertiser.source_advertiser_id.is_not(None), ~Advertiser.source_advertiser_id.startswith("unverified:"),
            or_(Ad.page_id.is_(None), Ad.page_id == "", Ad.page_id != Advertiser.source_advertiser_id, Ad.source != Advertiser.source))).all()
        for ad, old in mismatched:
            identity = str(ad.page_id).strip() if ad.page_id and str(ad.page_id).strip() else f"unverified:ad:{ad.external_id}"
            ad.advertiser_id = resolve_values(db, ad.source, identity, old.name, ad.platform, ad.country, old.page_url).id
        if not db.get(WorkspaceMigration, IDENTITY_VERSION):
            db.add(WorkspaceMigration(version=IDENTITY_VERSION))


def single_role(db):
    """Account policy 2026-10-19: one account type (MKT), active on registration. Idempotent; no user, session,
    history or Ads row is deleted. Former ADMIN accounts keep their data and become ordinary MKT accounts; pending
    registrations (old approval flow) become active. Locked accounts stay locked (internal CLI unlocks)."""
    db.execute(text("UPDATE users SET role = 'MKT' WHERE role <> 'MKT'"))
    db.execute(text("UPDATE users SET status = 'active' WHERE status = 'pending'"))
    if db.bind.dialect.name == "postgresql":
        db.execute(text("ALTER TABLE users DROP CONSTRAINT IF EXISTS users_role_check"))
        db.execute(text("ALTER TABLE users ADD CONSTRAINT users_role_check CHECK (role = 'MKT')"))
    if not db.get(WorkspaceMigration, SINGLE_ROLE_VERSION):
        db.add(WorkspaceMigration(version=SINGLE_ROLE_VERSION))


if __name__ == "__main__":
    migrate()
    print(f"Applied {VERSION}, {IDENTITY_VERSION} (additive; existing Ads retained)")
