"""Offline checks for app.visual (image search): validation, hashing invariances, SSRF guard, index → search on a
throw-away SQLite DB with real stored thumbnails, tiers, price comparison, API surface. No network, no model.
Run: cd apps/api && .venv/Scripts/python test_image_search.py"""
import glob
import io
import os
import tempfile
from datetime import datetime

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/vis.db"
os.environ["DATA_DIR"] = _tmp
os.environ["DISABLE_SCHEDULER"] = "1"
os.environ["STORAGE_BACKEND"] = "local"
os.environ["IMAGE_EMBED_ENABLED"] = "0"

from PIL import Image  # noqa: E402

from app import models  # noqa: E402,F401
from app.db import SessionLocal, auto_migrate  # noqa: E402
from app.models import Ad, Advertiser, Creative, Product  # noqa: E402
from app.storage import store  # noqa: E402
from app.visual import service  # noqa: E402
from app.visual.service import EXACT, LIKELY_SAME, SIMILAR, ImageError, QueryImage, check_public_host, fetch_image, open_image  # noqa: E402

auto_migrate()
THUMBS = sorted(glob.glob(os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "thumbs", "*", "*.jpg")))[:40]
assert len(THUMBS) >= 10, "needs some stored thumbnails in apps/api/data/thumbs"


def png(w=300, h=200, color=(200, 30, 30)):
    im = Image.new("RGB", (w, h), color)
    b = io.BytesIO()
    im.save(b, "PNG")
    return b.getvalue()


# --- 1. validation (real MIME, size, pixels)
for bad in (b"", b"not an image", b"GIF89a" + b"\0" * 10):
    try:
        open_image(bad)
        raise AssertionError("accepted junk")
    except ImageError as e:
        assert e.code in ("empty_image", "not_an_image"), e.code
assert open_image(png()).size == (300, 200)
big = io.BytesIO()
Image.new("L", (1, 1)).save(big, "TIFF")
try:
    open_image(big.getvalue())
    raise AssertionError
except ImageError as e:
    assert e.code == "unsupported_format"
service.MAX_PIXELS = 1000
try:
    open_image(png(100, 100))
    raise AssertionError
except ImageError as e:
    assert e.code == "too_many_pixels"
service.MAX_PIXELS = 30_000_000

# --- 2. hash invariances on a real thumbnail: resize / recompress stay EXACT, a crop stays SIMILAR, another picture is far
raw = open(THUMBS[0], "rb").read()
q0 = QueryImage(raw)
im = Image.open(io.BytesIO(raw)).convert("RGB")


def variant(fn, fmt="JPEG", **kw):
    b = io.BytesIO()
    fn(im.copy()).save(b, fmt, **kw)
    return QueryImage(b.getvalue())


small = variant(lambda x: x.resize((x.width // 2, x.height // 2)))
lowq = variant(lambda x: x, quality=35)
webp = variant(lambda x: x, fmt="WEBP", quality=70)
# measured on 60 stored thumbs: resize / q35 ≤ 1 bit; 5 % border crop median 8 (p90 13); 10 % crop median 16 → embeddings' job
crop = variant(lambda x: x.crop((x.width // 20, x.height // 20, x.width * 19 // 20, x.height * 19 // 20)))
other = QueryImage(open(THUMBS[5], "rb").read())
for v in (small, lowq, webp):
    assert service.hamming(q0.phash, v.phash) <= service.EXACT_DIST, (v.phash, q0.phash)
assert min(service.hamming(q0.phash, crop.phash), service.hamming(q0.dhash, crop.dhash)) <= service.SIMILAR_DIST
assert service.hamming(q0.phash, other.phash) > service.SIMILAR_DIST
assert q0.sha256 == QueryImage(raw).sha256  # deterministic normalisation
assert QueryImage(raw, crop=(0.1, 0.1, 0.8, 0.8)).width == im.width * 8 // 10 + (im.width * 9 // 10 - im.width // 10 - im.width * 8 // 10)

# --- 3. SSRF guard
for host in ("localhost", "127.0.0.1", "10.1.2.3", "192.168.1.1", "169.254.169.254", "::1", "0.0.0.0"):
    try:
        check_public_host(host)
        raise AssertionError(host)
    except ImageError as e:
        assert e.code in ("url_private_address", "url_unresolvable"), (host, e.code)
for url in ("ftp://example.com/a.jpg", "file:///etc/passwd", "http:///x", "javascript:alert(1)"):
    try:
        fetch_image(url)
        raise AssertionError(url)
    except ImageError as e:
        assert e.code in ("url_invalid", "url_private_address", "url_unresolvable"), (url, e.code)

# --- 4. index + search on SQLite with real thumbs copied into the temp store
with SessionLocal() as db:
    adv = Advertiser(name="Shop A", platform="facebook")
    db.add(adv)
    db.flush()
    prod = Product(product_code="PRD_1", canonical_name="lucky bracelet", country="PH", is_demo=False)
    db.add(prod)
    db.flush()
    for i, f in enumerate(THUMBS[:20]):
        data = open(f, "rb").read()
        key = f"thumbs/{i:02d}/t{i}.jpg"
        store().put(key, data, "image/jpeg")
        ad = Ad(external_id=f"x{i}", source="meta_library", platform="facebook", product_id=prod.id if i < 3 else None, advertiser_id=adv.id,
                country="PH", price=10.0 + i if i % 2 == 0 else None, currency="USD", search_text=f"lucky bracelet item {i}", network="meta",
                channel="ads", is_active=True, first_seen_at=datetime(2026, 9, 1), last_seen_at=datetime(2026, 10, 1))
        db.add(ad)
        db.flush()
        db.add(Creative(ad_id=ad.id, product_id=ad.product_id, type="image", source_url=f"https://cdn/{i}.jpg", status="pending", thumb_key=key,
                        network="meta", collected_at=datetime(2026, 10, 1)))
    db.commit()
r = service.index_pending(limit=100, eu_limit=0)
assert r["creatives"] == 20 and r["failed"] == 0, r
assert service.index_pending(limit=100, eu_limit=0)["creatives"] == 0  # idempotent
with SessionLocal() as db:
    st = service.status(db)
    assert st["creatives"]["indexed"] == 20 and st["embeddings"] == 0, st
    service.HASH_INDEX.refresh(db, force=True)
    assert len(service.HASH_INDEX.ids) == 20
    # same bytes → EXACT (sha256 + hash); resized copy → EXACT; crop → still found; unknown → truly empty
    res = service.search(db, QueryImage(open(THUMBS[0], "rb").read()))
    top = res["rows"][0]
    assert res["total"] >= 1 and top["tier"] == EXACT and top["similarity"] == 1.0 and top["creative_id"], res["rows"][:1]
    assert top["thumb"].startswith("/api/media/thumbs/") and top["price"] == 10.0 and top["currency"] == "USD"
    assert top["kind"] == "ad" and top["product_id"] == prod.id and res["engine"]["hash"] is True
    assert service.search(db, small)["rows"][0]["tier"] == EXACT
    assert service.search(db, crop)["total"] >= 1
    empty = service.search(db, QueryImage(png(400, 400, (0, 120, 255))))
    assert empty["total"] == 0 and empty["rows"] == [] and empty["tiers"] == {EXACT: 0, LIKELY_SAME: 0, SIMILAR: 0}  # no invented hits
    # modes + filters
    assert service.search(db, q0, mode="exact")["rows"][0]["tier"] == EXACT
    assert service.search(db, q0, mode="find_suppliers")["total"] == 0  # meta ads are not suppliers
    assert service.search(db, q0, country="DE")["total"] == 0 and service.search(db, q0, country="ph")["total"] >= 1
    assert service.search(db, q0, keywords=["bracelet"])["total"] >= 1 and service.search(db, q0, keywords=["zzz"])["total"] == 0
    assert service.search(db, q0, price_min=100)["total"] == 0
    assert all(x["creative_id"] != top["creative_id"] for x in service.search(db, q0, exclude=("creative", top["creative_id"]))["rows"])
    assert len(service.search(db, q0, limit=1)["rows"]) == 1
    # hash-only engine never claims LIKELY_SAME without metadata agreement
    for row in service.search(db, crop)["rows"]:
        assert row["tier"] in (EXACT, LIKELY_SAME, SIMILAR)
        if row["tier"] == LIKELY_SAME:
            assert row["product_id"] == prod.id

# --- 5. price comparison: real prices only, VAT / basis split, total only with shipping, stale flag
from app.visual.service import compare_prices  # noqa: E402

fx = {"EUR": (1.0, None), "USD": (1.1, "2026-10-08")}
rows = [
    {"tier": EXACT, "price": 11.0, "currency": "USD", "price_eur": {"eur": 10.0}, "price_basis": "ad_offer", "price_includes_vat": None,
     "shipping_price": None, "observed_at": datetime(2026, 10, 8)},
    {"tier": LIKELY_SAME, "price": 22.0, "currency": "EUR", "price_eur": {"eur": 22.0}, "price_basis": "retail", "price_includes_vat": True,
     "shipping_price": 3.0, "observed_at": datetime(2026, 8, 1)},
    {"tier": EXACT, "price": None, "currency": None, "price_eur": None, "price_basis": "ad_offer", "price_includes_vat": None,
     "shipping_price": None, "observed_at": datetime(2026, 10, 8)},
    {"tier": SIMILAR, "price": 5.0, "currency": "EUR", "price_eur": {"eur": 5.0}, "price_basis": "wholesale_per_unit", "price_includes_vat": False,
     "shipping_price": None, "observed_at": None},
]
cp = compare_prices(rows, fx)
assert cp["same_product"]["stats"] == {"offers": 2, "with_price_eur": 2, "min_eur": 10.0, "max_eur": 22.0, "median_eur": 16.0, "spread_pct": 120.0,
                                       "freshest": datetime(2026, 10, 8), "stalest": datetime(2026, 8, 1)}, cp["same_product"]["stats"]
assert len(cp["same_product"]["rows"]) == 3 and cp["same_product"]["rows"][2]["price"] is None  # unpriced offer listed, never priced
assert cp["same_product"]["by_basis"]["retail_incl_vat"]["offers"] == 1 and cp["same_product"]["by_basis"]["retail_vat_unknown"]["offers"] == 1
assert cp["same_product"]["rows"][1]["total"] == 25.0 and cp["same_product"]["rows"][0]["total"] is None  # total only with shipping
assert cp["same_product"]["rows"][1]["stale"] is True and cp["same_product"]["rows"][0]["stale"] is False
assert cp["similar_products"]["rows"][0]["price_basis"] == "wholesale_per_unit" and cp["similar_products"]["stats"]["offers"] == 1
assert compare_prices([], fx)["same_product"]["stats"] == {"offers": 0, "with_price_eur": 0}

# --- 6. API surface on the SQLite app (TestClient, admin token)
os.environ["ADMIN_TOKEN"] = "t"
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

c = TestClient(app)
h = {"X-Admin-Token": "t"}
st = c.get("/api/search/images/status").json()
assert st["creatives"]["indexed"] == 20
r = c.post("/api/search/images", files={"file": ("a.jpg", raw, "image/jpeg")}, data={"mode": "visual"}, headers=h)
assert r.status_code == 200 and r.json()["rows"][0]["tier"] == EXACT and r.json()["request_id"], r.text
r = c.post("/api/search/images", files={"file": ("a.txt", b"hello", "text/plain")}, headers=h)
assert r.status_code == 400 and r.json()["detail"]["code"] == "not_an_image", r.text
r = c.post("/api/search/images/by-url", json={"image_url": "http://127.0.0.1/x.jpg"}, headers=h)
assert r.status_code == 400 and r.json()["detail"]["code"] == "url_private_address", r.text
r = c.post("/api/search/images/from-product", json={"product_id": 1, "mode": "similar_product"}, headers=h)
assert r.status_code == 200 and all(x["product_id"] != 1 for x in r.json()["rows"]), r.text  # own creatives excluded
r = c.post("/api/search/images/from-product", json={"creative_id": 1}, headers=h)
assert r.status_code == 200 and all(x["creative_id"] != 1 for x in r.json()["rows"])
assert c.post("/api/search/images/from-product", json={"product_id": 999}, headers=h).status_code == 404
assert c.post("/api/search/images/from-product", json={"product_id": 1}).status_code == 401  # admin token required
r = c.post("/api/products/compare-prices-by-image", files={"file": ("a.jpg", raw, "image/jpeg")}, headers=h)
assert r.status_code == 200 and "same_product" in r.json() and r.json()["same_product"]["stats"]["offers"] >= 1, r.text
r = c.get("/api/products/1/price-comparison")
assert r.status_code == 200 and "same_product" in r.json()
assert c.get("/api/products/1/similar").status_code == 200
r = c.post("/api/products/image-matches/review", json={"source": "product", "source_id": 1, "target_source": "creative", "target_id": 7, "status": "confirmed"}, headers=h)
assert r.status_code == 200 and r.json()["review_status"] == "confirmed"
r = c.get("/api/products/1/image-matches")
assert r.status_code == 200 and r.json()["reviewed"][0]["target_id"] == 7
print("image search: all offline checks passed (hash engine, SQLite, no network)")
