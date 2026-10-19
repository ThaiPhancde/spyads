"""Phase 9 performance check on the REAL database, READ ONLY (one transaction set READ ONLY, rolled back).

    python scripts/bench_personalization.py [--runs 5] [--out ../../docs/PHASE9_PERF.json]

Calls the personalization endpoints in-process as one active MKT: latency per endpoint (cold = caches cleared, warm =
repeated), every SQL statement timed (slow-query list ≥ SLOW_MS), process CPU time and RAM (Windows working set via
ctypes, no extra dependency)."""
import argparse
import ctypes
import json
import os
import random
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import event, select, text  # noqa: E402

from app import models  # noqa: E402,F401
from app import library as L, personal_search as PS, preferences as PF, recommend as R, similar as S  # noqa: E402
from app.db import SessionLocal, engine  # noqa: E402
from app.models import Ad, Product  # noqa: E402
from app.routers import intel  # noqa: E402
from app.workspace import routes as W  # noqa: E402
from app.workspace.models import User  # noqa: E402

SLOW_MS = 100
queries = defaultdict(lambda: {"n": 0, "total_ms": 0.0, "max_ms": 0.0})


@event.listens_for(engine, "before_cursor_execute")
def _start(conn, cursor, statement, params, context, executemany):
    conn.info.setdefault("t0", []).append(time.perf_counter())


@event.listens_for(engine, "after_cursor_execute")
def _end(conn, cursor, statement, params, context, executemany):
    ms = 1000 * (time.perf_counter() - conn.info["t0"].pop())
    key = " ".join(statement.split())[:220]
    q = queries[key]
    q["n"] += 1
    q["total_ms"] += ms
    q["max_ms"] = max(q["max_ms"], ms)


class PMC(ctypes.Structure):
    _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong), ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]


def memory_mb():
    if os.name != "nt":
        return None, None
    c = PMC(); c.cb = ctypes.sizeof(PMC)
    k32, psapi = ctypes.windll.kernel32, ctypes.windll.psapi
    k32.GetCurrentProcess.restype = ctypes.c_void_p  # 64-bit pseudo-handle: the default int return truncates it
    psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(PMC), ctypes.c_ulong]
    psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(c), c.cb)
    return round(c.WorkingSetSize / 2**20, 1), round(c.PeakWorkingSetSize / 2**20, 1)


def clear_caches():
    intel._SEARCH_CACHE.clear(); PS._FIT.clear(); R._MEMO.clear(); R._VEC.update(at=0.0, v={}); S._TEXT.update(at=0.0, v={})


def pct(xs, q):
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(q * len(xs)))], 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[3] / "docs" / "PHASE9_PERF.json"))
    args = ap.parse_args()
    rnd = random.Random(9)
    out = {"endpoints": {}}
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        uid = db.scalar(select(User.id).where(User.status == "active", User.role == "MKT").order_by(User.id))
        db.info["user_id"], db.info["role"] = uid, "MKT"
        user = {"id": uid, "role": "MKT"}
        pids = list(db.scalars(select(Product.id).where(Product.in_target.is_(True), Product.category.is_not(None))))
        aids = list(db.scalars(select(Ad.id).where(Ad.search_text.is_not(None))))
        sample_p, sample_a = rnd.sample(pids, args.runs), rnd.sample(aids, args.runs)
        cases = {
            "GET /api/search q=2 keywords": lambda i: intel.search(db=db, q="bracelet|sunglasses", limit=36),
            "GET /api/search (no keyword, whole catalogue)": lambda i: intel.search(db=db, limit=36),
            "GET /api/search mode=for_me": lambda i: intel.search(db=db, mode="for_me", sort="fit", limit=36),
            "GET /api/search mode=claimable": lambda i: intel.search(db=db, mode="claimable", sort="fit", limit=36),
            "GET /api/search mode=claimable page 2": lambda i: intel.search(db=db, mode="claimable", sort="fit", limit=36, offset=36),
            "GET /api/recommendations": lambda i: R.feed(db, uid, 40),
            "GET /api/preferences": lambda i: PF.view(db, uid),
            "GET /api/library": lambda i: L.library(db, uid, "my"),
            "GET /api/library?section=similar": lambda i: L.library(db, uid, "similar"),
            "GET /api/library/dashboard": lambda i: L.dashboard(db, uid),
            "GET /api/workspace/dashboard": lambda i: W.dashboard(user=user, db=db),
            "GET /api/similar/products/{id}": lambda i: S.similar_products(db, sample_p[i], uid),
            "GET /api/similar/ads/{id}": lambda i: S.similar_ads(db, sample_a[i]),
        }
        cpu0, wall0 = os.times(), time.perf_counter()
        rss0 = memory_mb()
        for name, fn in cases.items():
            clear_caches()
            t = time.perf_counter(); fn(0); cold = 1000 * (time.perf_counter() - t)
            warm, stmts = [], []
            for i in range(args.runs):
                n0 = sum(v["n"] for v in queries.values())
                t = time.perf_counter(); fn(i); warm.append(1000 * (time.perf_counter() - t))
                stmts.append(sum(v["n"] for v in queries.values()) - n0)
            out["endpoints"][name] = {"cold_ms": round(cold, 1), "warm_p50_ms": pct(warm, .5), "warm_p95_ms": pct(warm, .95),
                                      "warm_max_ms": round(max(warm), 1), "sql_per_call_max": max(stmts)}  # N+1 shows up here
            print(f"{name:48s} cold {cold:7.0f} ms   warm p50 {pct(warm, .5):6.0f}  p95 {pct(warm, .95):6.0f}   sql/call ≤ {max(stmts)}")
        cpu1, wall = os.times(), time.perf_counter() - wall0
        db.rollback()
    cpu = (cpu1.user - cpu0.user) + (cpu1.system - cpu0.system)
    rss1 = memory_mb()
    out["process"] = {"wall_s": round(wall, 1), "cpu_s": round(cpu, 1), "cpu_share_of_one_core": round(cpu / wall, 2),
                      "ram_start_mb": rss0[0], "ram_end_mb": rss1[0], "ram_peak_mb": rss1[1]}
    slow = sorted(((k, v) for k, v in queries.items() if v["max_ms"] >= SLOW_MS), key=lambda kv: -kv[1]["max_ms"])
    out["sql"] = {"statements": sum(v["n"] for v in queries.values()), "distinct": len(queries),
                  "slow_threshold_ms": SLOW_MS, "slow": [{"sql": k, "n": v["n"], "max_ms": round(v["max_ms"], 1),
                                                          "avg_ms": round(v["total_ms"] / v["n"], 1)} for k, v in slow[:15]]}
    print(json.dumps(out["process"], indent=1))
    for s in out["sql"]["slow"]:
        print(f"{s['max_ms']:7.0f} ms max  x{s['n']:<4d} {s['sql'][:150]}")
    Path(args.out).write_text(json.dumps(out, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
