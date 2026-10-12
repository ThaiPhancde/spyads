"""LIVE test of web image discovery against the running API + real SerpApi (spends quota: 1 search per new picture/params).
Steps: account usage before → upload → local search → live search → poll → same search again (must be cache) →
compare prices with the web job → verify one priced result on its page → save one result → account usage after.
Run: cd apps/api && .venv/Scripts/python test_lens_live.py <image file> [country]   (API on :8000, ADMIN_TOKEN in .env)"""
import json
import os
import sys
import time

import httpx

from app import models  # noqa: F401 — loads .env

API = os.getenv("API_URL", "http://127.0.0.1:8000")
H = {"X-Admin-Token": os.environ["ADMIN_TOKEN"]}
img, country = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 else "de")
c = httpx.Client(timeout=120)
acct = lambda: c.get(f"{API}/api/search/images/quota").json()  # noqa: E731
q0 = acct()
print("before: internal", q0["month_used"], "| SerpApi this_month_usage", (q0["account"] or {}).get("this_month_usage"))

up = c.post(f"{API}/api/search/images/upload", files={"file": (os.path.basename(img), open(img, "rb").read(), "image/jpeg")}, headers=H).json()
ref = up["image_ref"]
loc = c.post(f"{API}/api/search/images", data={"image_ref": ref, "limit": "10"}, headers=H).json()
print(f"local: {loc['total']} results, tiers {loc['tiers']}")

t = time.time()
j = c.post(f"{API}/api/search/images/live", data={"image_ref": ref, "country": country}, headers=H).json()
print("live start:", {k: j.get(k) for k in ("job_id", "status", "cached", "detail")})
while True:
    r = c.get(f"{API}/api/search/images/results/{j['job_id']}?limit=200").json()
    if r["status"] != "running":
        break
    time.sleep(1.5)
print(f"live done in {time.time() - t:.1f}s: status={r['status']} err={r.get('error_code')} listings={r['total_listings']} tiers={r['tiers']} "
      f"discovered={r['discovered']} in_db={r['in_database']} with_price={r['with_price']} api_calls={r['api_calls']}")
for x in r["rows"][:12]:
    print(f"  {x['tier']:<20} sim={x['similarity']} d={x['hash_distance']} cos={x['cosine']} | {(x['shop_name'] or '')[:18]:<18} | "
          f"{(x['title'] or '')[:50]:<50} | {x['price']} {x['currency'] or x['price_raw'] or ''} | thumb={'y' if x['thumbnail_url'] else 'n'}")

j2 = c.post(f"{API}/api/search/images/live", data={"image_ref": ref, "country": country}, headers=H).json()
print("same search again:", {k: j2.get(k) for k in ("job_id", "status", "cached")})

cp = c.post(f"{API}/api/products/compare-prices-by-image", data={"image_ref": ref, "lens_job_id": str(j["job_id"])}, headers=H).json()
print("compare: web", cp.get("web"), "| same-product stats", cp["same_product"]["stats"], "| similar stats", cp["similar_products"]["stats"])
web = [x for x in cp["same_product"]["rows"] + cp["similar_products"]["rows"] if x["kind"] == "web"]
for x in web[:6]:
    print(f"  price row: {x['tier']:<20} {x['source_label'][:18]:<18} {x['price']} {x['currency']} eur={x['price_eur']} thumb={'y' if x['thumb'] else 'n'} url={(x['url'] or '')[:60]}")

priced = [i for i, x in enumerate(r["rows"]) if x["price"] is not None and x.get("product_url")]
if priced:
    v = c.post(f"{API}/api/search/images/results/{j['job_id']}/verify", json={"index": priced[0]}, headers=H).json()
    print("verify price on page:", v.get("price_check"), "→", v.get("price"), v.get("currency"), "verified" if v.get("price_verified") else "not verified")
if r["rows"]:
    sv = c.post(f"{API}/api/search/images/results/{j['job_id']}/save", json={"index": 0}, headers=H).json()
    print("save first result:", sv)
time.sleep(3)
q1 = c.get(f"{API}/api/search/images/quota").json()
print("after: internal", q1["month_used"], "cache_hits", q1["month_cache_hits"], "| SerpApi this_month_usage", (q1["account"] or {}).get("this_month_usage"),
      "(account cached ≤10 min — see quota endpoint later)")
json.dump({"job": r, "compare": cp}, open(os.path.join(os.path.dirname(img), "live_result.json"), "w"), default=str, indent=1)
