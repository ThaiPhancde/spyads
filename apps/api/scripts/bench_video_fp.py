"""FP / FN of video fingerprint v1 (PIL-resize dHash) vs v2 (cell-mean dHash) on HUMAN-LABELLED video pairs.

    python scripts/bench_video_fp.py pairs.csv

pairs.csv: `a,b,same` — two local video files you are allowed to use and whether a person judged them the same video
(1 / 0). Include the hard cases: head trimmed, aspect changed, caption overlay, music swapped, re-compressed. Frames are
decoded with ffmpeg (no re-encode). A verdict of None (fewer than 2 usable frames on both) is reported separately.
"""
import csv
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import media as M  # noqa: E402


def fingerprints(path: Path) -> tuple[str | None, dict | None]:
    dur = M._probe(path).get("duration")
    frames = M._fp_frames(path, dur)
    return M.video_fingerprint(path, dur, frames)[0], M.video_fp_v2(frames, dur, "bench")


def main(csv_path: str) -> None:
    cache: dict[str, tuple] = {}
    score = {"v1": Counter(), "v2": Counter()}
    for row in csv.DictReader(open(csv_path, encoding="utf8")):
        same = row["same"].strip() in ("1", "true", "yes")
        fa, fb = (cache.setdefault(p, fingerprints(Path(p))) for p in (row["a"], row["b"]))
        for algo, verdict in (("v1", M.same_video(fa[0], fb[0])), ("v2", M.same_video_v2(fa[1], fb[1]))):
            score[algo]["unknown" if verdict is None else ("TP" if same else "FP") if verdict else ("FN" if same else "TN")] += 1
    for algo, c in score.items():
        known = c["TP"] + c["FP"] + c["FN"] + c["TN"]
        print(f"{algo}: TP {c['TP']} · FP {c['FP']} · FN {c['FN']} · TN {c['TN']} · unknown {c['unknown']} · "
              f"precision {c['TP'] / max(1, c['TP'] + c['FP']):.2f} · recall {c['TP'] / max(1, c['TP'] + c['FN']):.2f} · pairs {known}")


if __name__ == "__main__":
    main(sys.argv[1])
