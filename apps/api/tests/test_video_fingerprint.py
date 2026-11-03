"""Phase 2: 3-frame video fingerprint (20/50/80 %) as a creative-family signal — never a product merge."""
import shutil
import subprocess

import pytest
from sqlalchemy import func, select

from app import media
from app.db import SessionLocal
from app.models import Creative, Product

pytestmark = pytest.mark.skipif(not media.FFMPEG, reason="ffmpeg not available")


def make_video(path, src, vf="null"):
    subprocess.run([media.FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", src, "-vf", vf, "-pix_fmt", "yuv420p", str(path)],
                   check=True, timeout=60)
    return path


@pytest.fixture
def videos(tmp_path):
    src = "testsrc2=duration=5:size=320x240:rate=10"
    return {"a": make_video(tmp_path / "a.mp4", src),
            "a_small": make_video(tmp_path / "a_small.mp4", src, "scale=160:120"),  # same cut, other rendition
            "b": make_video(tmp_path / "b.mp4", src, "hflip,vflip"),  # other cut
            "black": make_video(tmp_path / "black.mp4", "color=black:duration=3:size=320x240:rate=10")}


def test_probe_reads_duration_without_ffprobe(videos, monkeypatch):
    monkeypatch.setattr(media, "FFPROBE", None)
    meta = media._probe(videos["a"])
    assert meta["width"] == 320 and meta["height"] == 240 and abs(meta["duration"] - 5) < 0.2


def test_fingerprint_frames_and_matching(videos):
    fp, conf = media.video_fingerprint(videos["a"], 5)
    assert conf == 1.0 and len(fp) == 50 and fp.count(":") == 2
    assert media.same_video(fp, media.video_fingerprint(videos["a_small"], 5)[0]) is True
    assert media.same_video(fp, media.video_fingerprint(videos["b"], 5)[0]) is False
    assert media.video_fingerprint(videos["black"], 3) == (None, None)  # blank frames are no evidence
    assert media.video_fingerprint(videos["a"], None) == (None, None)  # no duration: no positions to sample
    assert media.same_video(fp, None) is None and media.same_video("aa::", "aa::") is None  # < 2 frames: unknown


def test_family_uses_fingerprint_and_never_merges_products(clean_catalog, videos):
    fa = media.video_fingerprint(videos["a"], 5)[0]
    fb = media.video_fingerprint(videos["b"], 5)[0]
    fa_small = media.video_fingerprint(videos["a_small"], 5)[0]
    with SessionLocal() as db:
        p1, p2 = Product(product_code="P-A", canonical_name="Blender A"), Product(product_code="P-B", canonical_name="Blender B")
        db.add_all([p1, p2]); db.flush()
        cover = "f0f0f0f0f0f0f0f0"
        first = Creative(type="video", source_url="u1", product_id=p1.id, phash=cover, video_fp=fa, status="stored")
        db.add(first); db.flush()
        first.family_id = first.id
        # same cover, other cut → own family (the cover alone used to merge them)
        other_cut = Creative(type="video", source_url="u2", product_id=p1.id, phash=cover, video_fp=fb)
        # other cover, same frames (another product's ad re-uses the video) → same family
        reupload = Creative(type="video", source_url="u3", product_id=p2.id, phash="0f0f0f0f0f0f0f0f", video_fp=fa_small)
        # no fingerprint (poster only) → cover hash still decides
        poster_only = Creative(type="video", source_url="u4", product_id=p1.id, phash=cover)
        db.add_all([other_cut, reupload, poster_only]); db.flush()
        assert media.assign_family(db, other_cut) == other_cut.id
        assert media.assign_family(db, reupload) == first.id
        assert media.assign_family(db, poster_only) == first.id
        assert reupload.product_id == p2.id and db.scalar(select(func.count()).select_from(Product)) == 2


def test_process_stores_fingerprint_without_extra_download(clean_catalog, videos, tmp_path, monkeypatch):
    from app.storage import LocalStorage
    vault = LocalStorage(tmp_path / "vault")
    downloads = []
    def download(url, dest):
        downloads.append(url)
        shutil.copyfile(videos["a"], dest)
        return "ab" * 32, "video/mp4", dest.stat().st_size
    monkeypatch.setattr(media, "_download", download)
    monkeypatch.setattr(media, "store", lambda: vault)
    monkeypatch.setattr(media, "DATA_DIR", tmp_path)
    monkeypatch.setattr(media, "_skip_reason", lambda db, c: None)
    monkeypatch.setattr("app.retention.video_quota_left", lambda db, b: 1)
    monkeypatch.setattr(media, "publish", lambda *a, **k: None)
    with SessionLocal() as db:
        c = Creative(type="video", source_url="https://video.example/a.mp4")
        db.add(c); db.commit()
        cid = c.id
    media._process(cid)
    with SessionLocal() as db:
        c = db.get(Creative, cid)
        assert c.status == "stored", c.error
        assert downloads == ["https://video.example/a.mp4"]  # the one download the vault already made
        assert c.video_fp_confidence == 1.0 and media.same_video(c.video_fp, media.video_fingerprint(videos["a"], 5)[0])
        assert abs(c.duration_sec - 5) < 0.2


def test_migration_adds_fingerprint_columns(tmp_path):
    from sqlalchemy import create_engine, inspect, text
    from app.meta_collector_migrate import migrate
    eng = create_engine(f"sqlite:///{tmp_path}/old.db")
    with eng.begin() as conn:  # a pre-Phase-2 creatives table with a row in it
        conn.execute(text("CREATE TABLE creatives (id INTEGER PRIMARY KEY, phash VARCHAR(16))"))
        conn.execute(text("INSERT INTO creatives (phash) VALUES ('ffff')"))
    migrate(eng); migrate(eng)
    cols = {c["name"] for c in inspect(eng).get_columns("creatives")}
    assert {"video_fp", "video_fp_confidence"} <= cols
    with eng.connect() as conn:
        assert conn.execute(text("SELECT phash, video_fp FROM creatives")).one() == ("ffff", None)
