"""P3 — versioned video fingerprint v2 (cell-mean 9x8 dHash at 20/50/80 %), kept apart from v1 and the poster phash;
efg URL metadata stored as an unverified claim. Fixtures are frames drawn here (no third-party media)."""
import base64
import io
import json
import random
from datetime import datetime
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from app import media as M
from app.db import SessionLocal
from app.models import Creative


def frame(seed: int, size=(640, 360)) -> Image.Image:
    rnd = random.Random(seed)
    im = Image.new("RGB", size, tuple(rnd.randrange(256) for _ in range(3)))
    d = ImageDraw.Draw(im)
    for _ in range(14):
        x, y = rnd.randrange(size[0]), rnd.randrange(size[1])
        w, h = rnd.randrange(40, size[0] // 2), rnd.randrange(30, size[1] // 2)
        (d.ellipse if rnd.random() < 0.5 else d.rectangle)([x, y, x + w, y + h], fill=tuple(rnd.randrange(256) for _ in range(3)))
    return im


def jpg(im: Image.Image, q=92) -> bytes:
    b = io.BytesIO()
    im.convert("RGB").save(b, "JPEG", quality=q)
    return b.getvalue()


# ---------------------------------------------------------------- the frame hash
TRANSFORMS = {
    "recompressed": lambda im: Image.open(io.BytesIO(jpg(im, 25))),
    "downscaled": lambda im: im.resize((320, 180)),
    "aspect_changed": lambda im: im.resize((640, 480)),
    "caption_overlay": lambda im: (lambda c: (ImageDraw.Draw(c).rectangle([20, 300, 300, 340], fill=(255, 255, 255)), c)[1])(im.copy()),
    "border_cropped": lambda im: im.crop((16, 9, 624, 351)),
}


@pytest.mark.parametrize("name", TRANSFORMS)
def test_cellmean_hash_survives_common_edits(name):
    base = frame(1)
    assert M.hamming(M.cellmean_hash(jpg(base)), M.cellmean_hash(jpg(TRANSFORMS[name](base)))) <= M.V2_DISTANCE


def test_cellmean_hash_tells_different_frames_apart():
    hs = [M.cellmean_hash(jpg(frame(s))) for s in range(12)]
    assert min(M.hamming(a, b) for i, a in enumerate(hs) for b in hs[i + 1:]) > M.V2_DISTANCE


def test_v2_is_a_different_algorithm_from_v1():
    """Same bytes, two algorithms: the hashes are not comparable — v1 (PIL resize) and v2 (cell mean) differ."""
    b = jpg(frame(3))
    assert M.dhash(b)[0] != M.cellmean_hash(b)


# ---------------------------------------------------------------- the stored fingerprint
def test_fingerprint_document_is_versioned_and_blank_frames_stay_empty():
    blank = jpg(Image.new("RGB", (640, 360), (0, 0, 0)))
    fp = M.video_fp_v2([jpg(frame(1)), blank, jpg(frame(2))], 31.5, "stored_file", observed_at=datetime(2026, 11, 3, 9))
    assert (fp["algorithm"], fp["version"], fp["grid"], fp["frame_positions"]) == ("cellmean-dhash", 2, [9, 8], [0.2, 0.5, 0.8])
    assert fp["frame_hashes"][1] is None and all(fp["frame_hashes"][i] for i in (0, 2))
    assert (fp["duration"], fp["source"], fp["observed_at"], fp["confidence"]) == (31.5, "stored_file", "2026-11-03T09:00:00", 0.67)
    assert M.video_fp_v2([None, None, None], 10, "stored_file") is None


def test_only_same_algorithm_and_version_are_compared():
    a = M.video_fp_v2([jpg(frame(1)), jpg(frame(2)), jpg(frame(3))], 30, "stored_file")
    b = M.video_fp_v2([jpg(TRANSFORMS["recompressed"](frame(i))) for i in (1, 2, 3)], 30, "sd_rendition")
    c = M.video_fp_v2([jpg(frame(i)) for i in (7, 8, 9)], 30, "stored_file")
    assert M.same_video_v2(a, b) is True and M.same_video_v2(a, c) is False
    assert M.same_video_v2(a, {**b, "version": 3}) is None and M.same_video_v2(a, {**b, "algorithm": "other"}) is None
    assert M.same_video_v2(a, None) is None


def test_family_prefers_v2_and_never_compares_v1_with_v2(clean_catalog):
    a = M.video_fp_v2([jpg(frame(i)) for i in (1, 2, 3)], 30, "stored_file")
    with SessionLocal() as db:
        old = Creative(type="video", source_url="https://v.example/1.mp4", status="stored", family_id=77, video_fp="ffffffffffffffff:0:0", video_fp_v2=a)
        new = Creative(type="video", source_url="https://v.example/2.mp4", status="stored", video_fp="0000000000000000:1:1", video_fp_v2=M.video_fp_v2(
            [jpg(TRANSFORMS["downscaled"](frame(i))) for i in (1, 2, 3)], 30, "stored_file"))
        db.add_all([old, new])
        db.flush()
        assert M.assign_family(db, new) == 77                         # v2 says same video although the v1 strings disagree
        lone = Creative(type="video", source_url="https://v.example/3.mp4", status="stored", video_fp="ffffffffffffffff:0:0")
        db.add(lone)
        db.flush()
        assert M.assign_family(db, lone) == 77                        # v1 vs v1 still works for rows without v2


def test_poster_phash_is_never_rewritten_by_v2_backfill(clean_catalog, monkeypatch, tmp_path):
    vid = tmp_path / "v.mp4"
    vid.write_bytes(b"not-really-a-video")
    monkeypatch.setattr(M.store(), "path", lambda key: vid, raising=False)
    monkeypatch.setattr(M, "_local_copy", lambda c: vid)
    monkeypatch.setattr(M, "_fp_frames", lambda path, duration: [jpg(frame(1)), jpg(frame(2)), jpg(frame(3))])
    monkeypatch.setattr(M, "safe_get", lambda *a, **k: pytest.fail("the backfill must not touch the network"))
    with SessionLocal() as db:
        c = Creative(type="video", source_url="https://v.example/4.mp4", status="stored", storage_key="creatives/v.mp4", duration_sec=30, phash="1234567890abcdef")
        done = Creative(type="video", source_url="https://v.example/5.mp4", status="stored", storage_key="creatives/w.mp4", duration_sec=30,
                        video_fp_v2={"algorithm": "cellmean-dhash", "version": 2, "frame_hashes": [None, None, None]})
        remote = Creative(type="video", status="pending", source_url="https://video.example/x.mp4", duration_sec=30)
        db.add_all([c, done, remote])
        db.commit()
        out = M.fingerprint_v2_backfill(db, limit=10)
        db.refresh(c), db.refresh(remote)
        assert out == {"fingerprinted": 1, "skipped": 0}
        assert c.phash == "1234567890abcdef" and c.video_fp_v2["version"] == 2 and remote.video_fp_v2 is None


# ---------------------------------------------------------------- efg: a third-party claim, never a verified date
def efg_url(payload) -> str:
    raw = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"https://video.xx.fbcdn.net/v/t42/a.mp4?efg={raw}&oe=6800AABB"


def test_efg_is_recorded_as_an_unverified_claim():
    c = M.efg_claims(efg_url({"vencode_tag": "x", "asset_age_days": 41, "xpv_asset_id": 123456789, "duration_s": 31}),
                     observed_at=datetime(2026, 11, 3, 9))
    assert c == {"age_claim_days": 41, "xpv_asset_id": "123456789", "duration_s": 31.0, "claimed_at": "2026-11-03T09:00:00",
                 "evidence_type": "efg_url_metadata", "confidence": "unverified"}


@pytest.mark.parametrize("url", ["https://video.example/a.mp4", "https://x/a.mp4?efg=%%%not-base64", "https://x/a.mp4?efg=" + "A" * 9000,
                                 efg_url(["not", "an", "object"]), efg_url({"asset_age_days": "forty"}), None])
def test_efg_parsing_is_bounded_and_never_raises(url):
    out = M.efg_claims(url)
    assert out is None or out.get("age_claim_days") is None


def test_ingest_keeps_the_efg_claim_on_the_creative(clean_catalog, monkeypatch):
    from app import ingest
    from app.collector.contract import AdRecord, MediaItem
    monkeypatch.setattr(ingest, "write_raw_batch", lambda *a: "test/raw.json")
    monkeypatch.setattr("app.media.enqueue", lambda *a: None)
    rec = AdRecord(source="meta_library", source_ad_id="e1", platform="facebook", ad_text="Buy blender now", title="Portable electric blender",
                   media=[MediaItem("video", efg_url({"asset_age_days": 9, "xpv_asset_id": 5}))])
    with SessionLocal() as db:
        ingest.ingest_ads(db, [rec])
        db.commit()
        c = db.query(Creative).one()
        assert c.media_claims["age_claim_days"] == 9 and c.media_claims["confidence"] == "unverified"


# ---------------------------------------------------------------- real decoded frames (ffmpeg-made fixtures, no third-party media)
@pytest.mark.skipif(not M.FFMPEG, reason="ffmpeg not available")
def test_v2_on_decoded_videos(tmp_path):
    from test_video_fingerprint import make_video
    src = "testsrc2=duration=5:size=320x240:rate=10"
    v = {"a": make_video(tmp_path / "a.mp4", src),
         "rendition": make_video(tmp_path / "r.mp4", src, "scale=160:120"),
         "caption": make_video(tmp_path / "c.mp4", src, "drawbox=x=10:y=200:w=150:h=30:color=white:t=fill"),
         "aspect": make_video(tmp_path / "s.mp4", src, "scale=320:180"),
         "other": make_video(tmp_path / "b.mp4", src, "hflip,vflip"),
         "black": make_video(tmp_path / "k.mp4", "color=black:duration=3:size=320x240:rate=10")}
    fp = {k: M.video_fp_v2(M._fp_frames(p, 3 if k == "black" else 5), 5, "stored_file") for k, p in v.items()}
    assert all(M.same_video_v2(fp["a"], fp[k]) is True for k in ("rendition", "caption", "aspect"))
    assert M.same_video_v2(fp["a"], fp["other"]) is False and fp["black"] is None
