"""Plain-assert self-check for media.py rules: python test_media_rules.py (run from apps/api)."""
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def worker_smoke():
    """Runs in a child process against a throw-away sqlite DB + DATA_DIR (the engine URL is fixed at import)."""
    import hashlib

    from app import media
    from app.db import Base, SessionLocal, engine
    from app.models import Ad, Creative

    Base.metadata.create_all(engine)
    calls = []

    def fake_download(url, dest):  # same bytes whatever the URL
        calls.append(url)
        dest.write_bytes(b"x" * 100)
        return hashlib.sha256(dest.read_bytes()).hexdigest(), "image/jpeg", 100

    media._download = fake_download
    media.dhash = lambda b: ("0" * 16, 10, 10)  # not a real JPEG
    media._thumb_jpeg = lambda b, max_w=480: b
    u = "https://scontent.xx.fbcdn.net/v/t42/1_n.jpg?_nc_cat=1&oe=66AA"
    with SessionLocal() as db:
        db.add_all([Ad(id=1, external_id="a1", source="meta_library", platform="facebook"),
                    Ad(id=2, external_id="a2", source="meta_library", platform="facebook")])
        db.add_all([Creative(id=1, ad_id=1, type="image", network="meta", source_url=u),
                    Creative(id=2, ad_id=1, type="image", network="meta", source_url=u.replace("66AA", "66BB")),  # same URL, new signature
                    Creative(id=3, ad_id=1, type="image", network="meta", source_url="https://x/other.jpg"),  # same bytes, same ad
                    Creative(id=4, ad_id=2, type="image", network="meta", source_url="https://x/other2.jpg"),  # same bytes, other ad
                    Creative(id=5, ad_id=2, type="image", network="meta", source_url="https://x/dead.jpg?oe=1")])  # expired link
        db.commit()
    media.process_creative(1)
    with SessionLocal() as db:
        c = db.get(Creative, 1)
        assert c.status == "stored" and c.stored_bytes == 100 and c.error is None, (c.status, c.stored_bytes, c.error)
        assert media.store().exists(c.storage_key), c.storage_key
        key1 = c.storage_key
    media.process_creative(2)  # same ad + same stripped URL → row deleted without a download
    assert len(calls) == 1, calls
    media.process_creative(3)  # downloaded, same sha for the same ad → row deleted
    assert len(calls) == 2
    media.process_creative(4)  # other ad, same sha → shares the file
    with SessionLocal() as db:
        assert db.get(Creative, 2) is None and db.get(Creative, 3) is None
        c4 = db.get(Creative, 4)
        assert c4.status == "stored" and c4.storage_key == key1 and c4.stored_bytes == 100, (c4.status, c4.storage_key)
        assert db.scalar(__import__("sqlalchemy").select(__import__("sqlalchemy").func.count()).select_from(Creative)) == 3
    assert media.expire_dead_links() == 1
    with SessionLocal() as db:
        assert db.get(Creative, 5).status == "expired"
    assert len(media._inflight) == 0
    print("worker smoke OK")


if "--worker" in sys.argv:
    worker_smoke()
    sys.exit(0)

os.environ.setdefault("DATABASE_URL", "sqlite:///file:market_intel.db?mode=ro&uri=true")  # never writes the real DB

from app import media  # noqa: E402

past, future = f"{int(time.time()) - 3600:x}", f"{int(time.time()) + 86400:x}"
assert media.source_alive(f"https://video.fvii2-4.fna.fbcdn.net/o1/v/t2/f2/m366/AQP.mp4?_nc_cat=1&oe={past}") is False
assert media.source_alive(f"https://video.fvii2-4.fna.fbcdn.net/o1/v/t2/f2/m366/AQP.mp4?_nc_cat=1&oe={future}") is True
assert media.source_alive(f"https://v16m.tiktokcdn.com/abc/?x-expires={int(time.time()) - 10}") is False
assert media.source_alive(f"https://v16m.tiktokcdn.com/abc/?x-expires={int(time.time()) + 7200}") is True
assert media.source_alive("https://ae01.alicdn.com/kf/S1.jpg") is True  # no expiry → playable
assert media.source_alive(None) is False

a = "https://scontent.xx.fbcdn.net/v/t42.1790-2/123_n.mp4?_nc_cat=1&oe=66AA"
b = "https://scontent.xx.fbcdn.net/v/t42.1790-2/123_n.mp4?_nc_cat=2&oe=66BB"
assert media._strip(a) == media._strip(b) == "https://scontent.xx.fbcdn.net/v/t42.1790-2/123_n.mp4"
assert media._strip(a) != media._strip("https://scontent.xx.fbcdn.net/v/t42.1790-2/124_n.mp4?oe=66AA")
assert media._strip(None) == ""

assert media.FFMPEG and Path(media.FFMPEG).is_file(), media.FFMPEG
assert media._probe.__doc__ is None  # (guard against accidental signature drift: _probe must stay a plain function)

from app.db import SessionLocal, read_only  # noqa: E402
from app import retention  # noqa: E402

with SessionLocal() as db, read_only():
    t0 = time.perf_counter()
    u = retention.usage(db)
    dt = time.perf_counter() - t0
    t0 = time.perf_counter()
    retention.usage(db)
    dt2 = time.perf_counter() - t0
assert u["video"]["count"] + u["image"]["count"] == sum(b["videos"] + b["images"] for b in u["buckets"])
assert u["video"]["bytes"] + u["image"]["bytes"] == sum(b["bytes"] for b in u["buckets"])
print(f"usage(): {dt:.2f}s first call, {dt2:.2f}s second (cached files) · video {u['video']} · image {u['image']} · "
      f"pending {u['pending']} archived {u['archived']} pinned {u['pinned']} · today {u['today']['videos']}")
tmp = tempfile.mkdtemp(prefix="media_test_")
subprocess.run([sys.executable, __file__, "--worker"], check=True, cwd=os.path.dirname(os.path.abspath(__file__)),
               env={**os.environ, "DATABASE_URL": f"sqlite:///{tmp}/t.db", "DATA_DIR": f"{tmp}/data", "VOD": "0"})
print("OK")
