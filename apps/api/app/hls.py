"""Video on Demand: original → adaptive HLS ladder.

Each video becomes:
    hls/<sha>/master.m3u8          ← the player opens this; lists every rendition + bandwidth
    hls/<sha>/<h>p/index.m3u8      ← byte-range playlist of ~4 s segments
    hls/<sha>/<h>p/index.mp4       ← ONE fragmented-MP4 file per rendition (segments are byte ranges)

The browser (hls.js / Safari) measures throughput while playing and switches rendition per segment:
fast network → 540p, weak network → 360p (ABR). Segments load progressively as the video plays, so
nothing is downloaded up front. One file per rendition keeps R2 write operations tiny, and the top
rendition is itself a normal MP4 for "download".

Ladder is sized on the SHORT side (most ads are vertical 9:16) and never upscales.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

LADDER = [  # short side px, video kbps, max kbps
    (360, 350, 450),
    (540, 800, 1000),
]
AUDIO_KBPS = 64
SEGMENT_SECONDS = 4


def ffmpeg_exe() -> str | None:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def probe(src: Path) -> dict:
    """width / height / duration / has_audio from ffmpeg's own banner (works without ffprobe)."""
    exe = ffmpeg_exe()
    r = subprocess.run([exe, "-hide_banner", "-i", str(src)], capture_output=True, text=True, timeout=60)
    err = r.stderr
    out = {"width": None, "height": None, "duration": None, "has_audio": " Audio:" in err}
    m = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", err)
    if m:
        out["width"], out["height"] = int(m.group(1)), int(m.group(2))
    m = re.search(r"Duration: (\d+):(\d+):(\d+\.?\d*)", err)
    if m:
        out["duration"] = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    return out


def transcode(src: Path, out_dir: Path, timeout: int = 900) -> dict:
    """Create the HLS ladder in out_dir. Returns {renditions, width, height, duration, bytes}."""
    exe = ffmpeg_exe()
    if not exe:
        raise RuntimeError("ffmpeg không có trên máy (pip install imageio-ffmpeg hoặc cài ffmpeg)")
    info = probe(src)
    w, h = info["width"], info["height"]
    if not w or not h:
        raise RuntimeError("không đọc được kích thước video")
    short = min(w, h)
    rungs = [r for r in LADDER if r[0] <= short] or [LADDER[0]]
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    n = len(rungs)
    split = f"[0:v]split={n}" + "".join(f"[v{i}]" for i in range(n)) + ";"
    scales = []
    for i, (px, _, _) in enumerate(rungs):
        sc = f"scale={px}:-2" if w <= h else f"scale=-2:{px}"  # short side = px, keep aspect, even dims
        scales.append(f"[v{i}]{sc}[o{i}]")
    args = [exe, "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
            "-filter_complex", split + ";".join(scales)]
    var_map = []
    for i, (px, kbps, maxk) in enumerate(rungs):
        args += ["-map", f"[o{i}]"]
        if info["has_audio"]:
            args += ["-map", "0:a:0"]
        args += [f"-c:v:{i}", "libx264", f"-b:v:{i}", f"{kbps}k", f"-maxrate:v:{i}", f"{maxk}k",
                 f"-bufsize:v:{i}", f"{maxk * 2}k"]
        var_map.append(f"v:{i},a:{i},name:{px}p" if info["has_audio"] else f"v:{i},name:{px}p")
    args += ["-preset", "veryfast", "-profile:v", "main", "-pix_fmt", "yuv420p", "-sc_threshold", "0",
             "-force_key_frames", f"expr:gte(t,n_forced*{SEGMENT_SECONDS})"]
    if info["has_audio"]:
        args += ["-c:a", "aac", "-b:a", f"{AUDIO_KBPS}k", "-ac", "2"]
    args += ["-f", "hls", "-hls_time", str(SEGMENT_SECONDS), "-hls_playlist_type", "vod",
             "-hls_segment_type", "fmp4", "-hls_flags", "single_file+independent_segments",
             "-master_pl_name", "master.m3u8", "-var_stream_map", " ".join(var_map),
             str(out_dir / "%v" / "index.m3u8")]
    r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0 or not (out_dir / "master.m3u8").exists():
        raise RuntimeError(f"ffmpeg lỗi: {r.stderr[-400:]}")
    for pl in out_dir.rglob("*.m3u8"):  # Windows ffmpeg writes "360p\index.m3u8" — URLs need "/"
        pl.write_text(pl.read_text(encoding="utf-8").replace("\\", "/"), encoding="utf-8")
    total = sum(f.stat().st_size for f in out_dir.rglob("*") if f.is_file())
    return {"renditions": [f"{px}p" for px, _, _ in rungs], "width": w, "height": h, "duration": info["duration"],
            "bytes": total}


def files(out_dir: Path) -> list[tuple[Path, str]]:
    """(path, key-suffix) of every file to upload, playlists last so a player never sees a half-uploaded ladder."""
    all_ = [p for p in out_dir.rglob("*") if p.is_file()]
    media = [p for p in all_ if p.suffix != ".m3u8"]
    lists = sorted([p for p in all_ if p.suffix == ".m3u8"], key=lambda p: p.name == "master.m3u8")
    return [(p, p.relative_to(out_dir).as_posix()) for p in media + lists]


CONTENT_TYPES = {".m3u8": "application/vnd.apple.mpegurl", ".mp4": "video/mp4", ".m4s": "video/mp4"}


def content_type(p: Path) -> str:
    return CONTENT_TYPES.get(p.suffix, "application/octet-stream")


def top_rendition(renditions: list[str]) -> str:
    return max(renditions, key=lambda r: int(r.rstrip("p")))


def cleanup(path: Path):
    shutil.rmtree(path, ignore_errors=True) if path.is_dir() else (os.path.exists(path) and os.remove(path))
