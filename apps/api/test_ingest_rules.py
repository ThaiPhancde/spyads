"""Plain-assert checks for the ingest rules (audit 2026-10-07 §4). Run: .venv/Scripts/python.exe test_ingest_rules.py"""
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.collector.contract import AdRecord, MediaItem
from app.db import Base
from app.ingest import _attach_creatives, bare_url, classify_funnel, guess_product_name, parse_price
from app.markets import known_country
from app.models import Ad, Creative

# ---- 1. creative dedupe on the URL without its CDN signature
assert bare_url("https://scontent.xx.fbcdn.net/v/t42/a.mp4?_nc_cat=1&oh=abc&oe=123#x") == "https://scontent.xx.fbcdn.net/v/t42/a.mp4"
eng = create_engine("sqlite://")
Base.metadata.create_all(eng)
with Session(eng) as db:
    ad = Ad(external_id="1", source="meta_library", platform="facebook")
    db.add(ad)
    db.flush()
    db.add(Creative(ad_id=ad.id, type="video", source_url="https://cdn.x/v/a.mp4?oh=OLD", status="pending"))
    db.flush()
    rec = AdRecord(source="meta_library", source_ad_id="1", media=[
        MediaItem("video", "https://cdn.x/v/a.mp4?oh=NEW"),  # same asset, new signature → no new row
        MediaItem("image", "https://cdn.x/i/b.jpg?oh=1"),  # new asset → one row
    ])
    new = _attach_creatives(db, ad, rec)
    rows = db.scalars(select(Creative).where(Creative.ad_id == ad.id)).all()
    assert len(new) == 1 and len(rows) == 2, (new, rows)
    assert db.scalar(select(Creative.source_url).where(Creative.type == "video")) == "https://cdn.x/v/a.mp4?oh=NEW"  # pending row got the fresh URL
    assert _attach_creatives(db, ad, rec) == []  # idempotent

# ---- 2. product name never comes from the ad copy
copy = "Balikan nating muli ang patunay ni Gng. Santos — order now!"
r = AdRecord(source="x", source_ad_id="1", ad_text=copy, advertiser="Acme PH")
assert guess_product_name(r) == "Acme PH (chưa đặt tên)", guess_product_name(r)
r.landing_page = "https://www.facebook.com/acme/"  # social host → no slug
assert guess_product_name(r) == "Acme PH (chưa đặt tên)"
r.landing_page = "https://acme-shop.com/pages/magnetic-back-brace"
assert guess_product_name(r) == "magnetic back brace", guess_product_name(r)
r.product_name = "Posture Pro Belt"  # LLM hint wins
assert guess_product_name(r) == "Posture Pro Belt"
r2 = AdRecord(source="x", source_ad_id="2", ad_text=copy, title="Shop now", landing_page="https://www.amazon.ae/dp/B08LHSBG3R", advertiser=None)
assert guess_product_name(r2) == "Unknown (chưa đặt tên)", guess_product_name(r2)
for rec_ in (r, r2):
    assert "Balikan" not in guess_product_name(rec_)

# ---- 3. CTA → funnel (CTA first, landing host second)
assert classify_funnel("MESSAGE_PAGE", "https://acme-shop.com/p/x") == "mess"  # CTA beats the external landing
assert classify_funnel("LEARN_MORE", "https://acme-shop.com/p/x", None, "Send message") == "mess"
assert classify_funnel(None, None, None, "WhatsApp") == "mess"
assert classify_funnel(None, "https://acme-shop.com/", None, "Call now") == "mess"
assert classify_funnel("SHOP_NOW", "https://acme-shop.com/lp") == "ladi"
assert classify_funnel("SIGN_UP", "https://acme-shop.com/lp") == "ladi"
assert classify_funnel("SIGN_UP", None) == "form"
assert classify_funnel("SHOP_NOW", "https://play.google.com/store/apps/x") == "app"
assert classify_funnel(None, "https://www.amazon.ae/dp/B08LHSBG3R") == "other"
assert classify_funnel(None, "https://acme.myshopify.com/products/x") == "ladi"
assert classify_funnel(None, "https://wa.me/966500000000") == "mess"
assert classify_funnel("SHOP_NOW", "https://www.facebook.com/acme") == "other"

# ---- 4. listed price in the copy
cases = {
    "Only ₱189 today": (189.0, "PHP"), "PHP 189 COD": (189.0, "PHP"), "P189 free shipping": (189.0, "PHP"),
    "now 199 SAR": (199.0, "SAR"), "SAR 199 only": (199.0, "SAR"), "199 ر.س": (199.0, "SAR"),
    "AED 99 delivery": (99.0, "AED"), "1,299.00 AED": (1299.0, "AED"),
    "just $19.99": (19.99, "USD"), "€29 shipped": (29.0, "EUR"), "chỉ 199.000đ": (199000.0, "VND"), "giá 199k": (199000.0, None),
}
for text, want in cases.items():
    assert parse_price(text) == want, (text, parse_price(text))
for text in ("iPhone 15 Pro", "P2P lending", "no price here", "call 0917 123 4567", ""):
    assert parse_price(text) is None, (text, parse_price(text))

# ---- 5. 'ALL' is a scope, not a country
assert known_country(AdRecord(source="x", source_ad_id="1", country="ALL", countries=["ALL", "ph"])) == "PH"
assert known_country(AdRecord(source="x", source_ad_id="1", countries=["ALL"])) is None
print("test_ingest_rules: OK")
