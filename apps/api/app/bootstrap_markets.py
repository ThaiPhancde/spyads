"""Market starter pack: tracked keyword queries per market (local language), run once now.

Keywords target e-commerce sellers (COD / free delivery / offer wording), which is what MKT Mess and
MKT Ladi teams research. The scheduler re-runs them every `EVERY` minutes afterwards.

Usage (API must be running):  python -m app.bootstrap_markets [--limit 40] [--markets SA,AE]
"""
import argparse
import time

import os
import httpx

API = "http://localhost:8000"
EVERY = 360

# (keywords, every_minutes) per market — local-language e-commerce wording (COD / free delivery / offers)
KEYWORDS = {
    # Philippines — top priority, every 30 min (Taglish + English COD wording)
    "PH": (["cash on delivery", "COD nationwide", "free shipping nationwide", "libreng shipping", "order na"], 30),
    # Middle East — hourly
    "SA": (["الدفع عند الاستلام", "توصيل مجاني", "free delivery"], 60),
    "AE": (["الدفع عند الاستلام", "توصيل مجاني", "cash on delivery"], 60),
    "KW": (["الدفع عند الاستلام", "توصيل مجاني"], 60),
    "QA": (["الدفع عند الاستلام", "توصيل مجاني"], 90),
    "OM": (["الدفع عند الاستلام", "توصيل مجاني"], 90),
    "BH": (["الدفع عند الاستلام", "توصيل مجاني"], 90),
    "JO": (["الدفع عند الاستلام"], 120),
    "EG": (["الدفع عند الاستلام"], 120),
    "IQ": (["الدفع عند الاستلام"], 120),
    # US
    "US": (["free shipping", "shop now", "limited time offer"], 60),
    # Europe (+UK)
    "GB": (["free delivery", "shop now"], 90),
    "DE": (["kostenloser Versand", "jetzt kaufen"], 120),
    "FR": (["livraison gratuite", "paiement à la livraison"], 120),
    "IT": (["spedizione gratuita", "pagamento alla consegna"], 120),
    "ES": (["envío gratis", "contra reembolso"], 120),
    "NL": (["gratis verzending"], 180),
    "PL": (["darmowa dostawa", "płatność przy odbiorze"], 180),
    "SE": (["fri frakt"], 180),
    "RO": (["livrare gratuita", "plata la livrare"], 180),
    # Australia / NZ
    "AU": (["free shipping", "afterpay"], 90),
    "NZ": (["free shipping"], 180),
    # Vietnam
    "VN": (["freeship", "thanh toán khi nhận hàng", "giảm giá"], 120),
    # Worldwide (country = ALL in the Ad Library)
    "ALL": (["free shipping", "cash on delivery"], 120),
}


def _env_overrides():
    """KW_<MARKET>=kw1|kw2 and optional KW_<MARKET>_EVERY=minutes in the root .env replace the defaults above."""
    import os
    from pathlib import Path

    f = Path(__file__).resolve().parents[3] / ".env"
    vals = dict(os.environ)
    if f.is_file():
        for line in f.read_text(encoding="utf8").splitlines():
            k, sep, v = line.partition("=")
            if sep and not line.lstrip().startswith("#"):
                vals.setdefault(k.strip(), v.split(" #")[0].strip())
    for m in list(KEYWORDS) + [k[3:] for k in vals if k.startswith("KW_") and not k.endswith("_EVERY")]:
        if vals.get(f"KW_{m}"):
            KEYWORDS[m] = ([x.strip() for x in vals[f"KW_{m}"].split("|") if x.strip()],
                           int(vals.get(f"KW_{m}_EVERY") or KEYWORDS.get(m, ([], EVERY))[1]))


_env_overrides()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--markets", default="")
    ap.add_argument("--no-run", action="store_true", help="only create / update queries; let the scheduler run them")
    args = ap.parse_args()
    markets = [m.strip().upper() for m in args.markets.split(",") if m.strip()] or list(KEYWORDS)
    c = httpx.Client(base_url=API, timeout=60, headers={"X-Admin-Token": os.getenv("ADMIN_TOKEN", "")})
    col = c.get("/api/collector").json()
    meta = next(x for x in col["connectors"] if x["adapter"] == "meta_library")
    wanted = {(kw, m) for m in KEYWORDS for kw in KEYWORDS[m][0]}
    existing = {(q["query"], q["countries"][0] if q["countries"] else ""): q for q in col["tracked_queries"]}
    # markets outside the plan (e.g. SEA): keep their data, stop re-collecting
    for (kw, m), q in existing.items():
        if (kw, m) not in wanted and q["enabled"]:
            c.patch(f"/api/collector/queries/{q['id']}", json={"enabled": False})
            print(f"paused {m} {kw!r}")
    ids = []
    for m in markets:
        kws, every = KEYWORDS[m]
        for kw in kws:
            q = existing.get((kw, m))
            if q:
                c.patch(f"/api/collector/queries/{q['id']}", json={"enabled": True, "every_minutes": every})
                continue
            r = c.post("/api/collector/queries", json={"connector_id": meta["id"], "query": kw, "countries": [m],
                                                     "every_minutes": every, "limit": args.limit, "user": "starter-pack"})
            ids.append((r.json()["id"], m, kw))
    print(f"created {len(ids)} tracked queries")
    if args.no_run:
        return
    for qid, m, kw in ids:
        c.post(f"/api/collector/queries/{qid}/run")
        t0 = time.time()
        while time.time() - t0 < 300:
            time.sleep(4)
            q = next((x for x in c.get("/api/collector").json()["tracked_queries"] if x["id"] == qid), None)
            if q and q["last_run_at"]:
                print(f"{m} {kw!r}: {q['last_count']} ads, {q['last_new']} new" + (f"  ERROR {q['last_error'][:120]}" if q["last_error"] else ""), flush=True)
                break
        else:
            print(f"{m} {kw!r}: timeout", flush=True)


if __name__ == "__main__":
    main()
