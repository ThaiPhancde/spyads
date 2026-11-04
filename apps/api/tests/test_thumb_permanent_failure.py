"""A cover that can never become a thumbnail (not an image / over the size cap) is recorded once and left alone: the media
tick used to retry it every few minutes for a whole day (843 'thumb grab failed' lines in one worker log)."""
from datetime import datetime

import pytest
from sqlalchemy import select

from app import media
from app.db import SessionLocal
from app.models import Creative
from app.netsafe import FetchError, Fetched


@pytest.fixture(autouse=True)
def isolated(clean_catalog):
    yield


def pending(url="https://img.example/a.jpg"):
    with SessionLocal() as db:
        c = Creative(type="image", source_url=url, status="pending", collected_at=datetime.utcnow())
        db.add(c)
        db.commit()
        return c.id


def error(cid):
    with SessionLocal() as db:
        return db.get(Creative, cid).error


def test_not_an_image_is_recorded_as_a_permanent_thumb_failure(monkeypatch):
    cid = pending()
    monkeypatch.setattr(media, "safe_get", lambda *a, **k: Fetched(200, {}, b"<html>not an image</html>", "https://img.example/a.jpg"))
    assert media.grab_thumb(cid) is None
    assert error(cid).startswith("thumb:")


def test_too_large_is_recorded_as_a_permanent_thumb_failure(monkeypatch):
    cid = pending()
    def big(*a, **k):
        raise FetchError("too_large", "> 20 MB")
    monkeypatch.setattr(media, "safe_get", big)
    assert media.grab_thumb(cid) is None
    assert error(cid).startswith("thumb:")


def test_a_network_blip_is_not_permanent(monkeypatch):
    import httpx
    cid = pending()
    def blip(*a, **k):
        raise httpx.ConnectTimeout("slow")
    monkeypatch.setattr(media, "safe_get", blip)
    assert media.grab_thumb(cid) is None
    assert error(cid) is None


def test_the_retry_tick_skips_permanent_failures_but_keeps_the_rest(monkeypatch):
    bad, good = pending("https://img.example/bad.jpg"), pending("https://img.example/good.jpg")
    with SessionLocal() as db:
        db.get(Creative, bad).error = "thumb: not an image"
        db.commit()
    queued = []
    monkeypatch.setattr(media, "enqueue", lambda ids, *a, **k: queued.extend(ids))
    monkeypatch.setattr(media, "expire_dead_links", lambda *a, **k: None)
    media.enqueue_pending()
    assert good in queued and bad not in queued


def test_the_on_demand_route_still_retries_a_marked_creative(monkeypatch):
    """Viewing a card asks again (the link may have been fixed): grab_thumb itself never refuses a marked row."""
    cid = pending()
    with SessionLocal() as db:
        db.get(Creative, cid).error = "thumb: not an image"
        db.commit()
    from PIL import Image
    import io
    b = io.BytesIO()
    Image.new("RGB", (20, 20), (200, 10, 10)).save(b, "PNG")
    monkeypatch.setattr(media, "safe_get", lambda *a, **k: Fetched(200, {}, b.getvalue(), "https://img.example/a.jpg"))
    monkeypatch.setattr(media, "store", lambda: type("S", (), {"exists": lambda s, k: False, "put": lambda s, *a: None})())
    assert media.grab_thumb(cid)
    with SessionLocal() as db:
        assert db.get(Creative, cid).thumb_key
