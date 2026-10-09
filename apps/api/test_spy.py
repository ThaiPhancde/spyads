"""Offline check for the competitor-spy parsers (prices in copy, landing-page price / reviews).
Run: cd apps/api && python test_spy.py"""
from app.ingest import parse_price
from app.services.spy import SKIP_HOST, norm_url, parse_landing

# --- price in the ad copy (PH / VN / ME / US formats, and the usual false positives)
assert parse_price("Chỉ 199k", "VN") == (199000, "VND") and parse_price("Giá 299.000đ") == (299000, "VND")
assert parse_price("₱499 only") == (499, "PHP") and parse_price("P499", "PH") == (499, "PHP") and parse_price("P499", "US") is None
assert parse_price("19.98 USD") == (19.98, "USD") and parse_price("SALE 60% OFF – $17.99 Only!") == (17.99, "USD")
assert parse_price("Giá: 2tr") == (2_000_000, "VND") and parse_price("Dưới 10 triệu") is None  # no price word around it
assert parse_price("AED 99") == (99, "AED") and parse_price("Rp 150.000") == (150000, "IDR")
for junk in ("4K video", "SPF 50+ PA++++", "100% cotton", "v1.5 update", "Buy 1 Take 1"):
    assert parse_price(junk) is None, junk

# --- landing page: JSON-LD offers + reviews, og:price, Shopify currency; '1.016.000' thousands
LD = """<html><head><script type="application/ld+json">{"@context":"https://schema.org","@type":"Product","name":"X",
"offers":{"@type":"Offer","price":"1.016.000","priceCurrency":"VND"},
"aggregateRating":{"@type":"AggregateRating","ratingValue":"4.7","reviewCount":"213"},
"review":[{"@type":"Review","reviewBody":"Size runs small but works","reviewRating":{"ratingValue":4},"author":{"name":"A"}}]}</script>
<script>Shopify.currency = {"active":"VND","rate":"1.0"};</script></head></html>"""
r = parse_landing(LD, "https://x.store/products/y")
assert r["price"] == 1_016_000 and r["currency"] == "VND" and r["rating"] == 4.7 and r["review_count"] == 213, r
assert r["reviews"][0]["text"].startswith("Size runs") and r["reviews"][0]["rating"] == 4
OG = '<meta property="og:price:amount" content="74.99"><meta property="og:price:currency" content="USD">'
assert parse_landing(OG, "u")["price"] == 74.99 and parse_landing(OG, "u")["currency"] == "USD"
assert parse_landing("<html>nothing</html>", "u")["price"] is None

# --- which landing URLs are even worth fetching
assert SKIP_HOST.search("fb.com") and SKIP_HOST.search("api.whatsapp.com") and SKIP_HOST.search("play.google.com")
assert not SKIP_HOST.search("gulfro.com") and not SKIP_HOST.search("axzenia.com")
assert norm_url("https://www.Gulfro.com/products/x/?utm=1#a") == "https://gulfro.com/products/x"
print("test_spy OK")

# --- Vietnamese count words are not prices; "k" needs a Vietnamese ad to mean ×1000 đ
assert parse_price("chứa 2,4 triệu hạt Cica Exosome") is None and parse_price("Hơn 15 triệu chai đã được bán") is None
assert parse_price("Giá cực kỳ tốt, chỉ hơn 9 triệu đồng") == (9_000_000, "VND") and parse_price("ghế massage chỉ 5,99 triệu") == (5_990_000, "VND")
assert parse_price("đền 100 triệu nếu hàng giả") is None and parse_price("99k followers") is None and parse_price("only 99k") is None
assert parse_price("99k/ 6 hũ miễn ship") == (99000, "VND")
print("test_spy VN-count OK")
assert parse_price("GIẢM NGAY 80.000Đ Voucher") == (80000, "VND") and parse_price("Freeship 0Đ toàn quốc") is None
print("test_spy Đ OK")
assert parse_landing('<span class="product-price">$29.99</span>', "u")["price"] == 29.99
assert parse_landing('<div class="price-box">Group Buy Price</div><p class="price">₱1,299</p>', "u")["currency"] == "PHP"
print("test_spy price-class OK")
