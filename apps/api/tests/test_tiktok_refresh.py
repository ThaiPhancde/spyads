"""media.refresh_tiktok_links: dead links refreshed, removed ads deleted, never a mass delete on a bad answer."""
from app import media
from app.db import SessionLocal
from app.models import Ad, Creative


def _seed(n):
    with SessionLocal() as db:
        ads = [Ad(external_id=f"tt{i}", source="tiktok_top_ads", platform="tiktok") for i in range(n)]
        db.add_all(ads)
        db.flush()
        db.add_all([Creative(ad_id=a.id, type="video", source="tiktok_top_ads", source_ad_id=f"m{i}", status="expired",
                             source_url="https://v16m.tiktokcdn.com/0123456789abcdef0123456789abcdef/00000001/video/x.mp4")
                    for i, a in enumerate(ads)])
        db.commit()
        return [a.id for a in ads]


def _count_ads():
    with SessionLocal() as db:
        return db.query(Ad).count()


def test_refreshes_dead_links_and_deletes_removed_ads(clean_catalog, monkeypatch):
    ids = _seed(8)
    gone = {f"m{i}" for i in (0, 1)}

    def fake(c):
        if c.source_ad_id in gone:
            return False
        c.source_url = "https://v16m.tiktokcdn.com/0123456789abcdef0123456789abcdef/ffffffff/video/new.mp4"
        return True

    monkeypatch.setattr(media, "refresh_source", fake)
    r = media.refresh_tiktok_links()
    assert r == {"checked": 8, "fresh": 6, "failed": 0, "gone_ads_deleted": 2}
    with SessionLocal() as db:
        assert {a.id for a in db.query(Ad)} == set(ids[2:])
        assert all(media.source_alive(c.source_url) for c in db.query(Creative))


def test_no_mass_delete_when_source_answers_wrong(clean_catalog, monkeypatch):
    _seed(8)
    monkeypatch.setattr(media, "refresh_source", lambda c: False)  # e.g. API changed: every ad looks gone
    assert media.refresh_tiktok_links()["gone_ads_deleted"] == 0
    assert _count_ads() == 8
