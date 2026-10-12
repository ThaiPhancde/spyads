"""Offline checks for the public-data-first EU connector registry and the JSON-LD product-page parser (fixtures = schema.org examples + real-world shapes: @graph, AggregateOffer, string prices).
Run: cd apps/api && .venv/Scripts/python test_eu_public_sources.py"""
import asyncio
import os

os.environ["DATABASE_URL"] = "sqlite://"
os.environ["IMAGE_EMBED_ENABLED"] = "0"
import httpx  # noqa: E402

from app.eu_market import connectors  # noqa: E402
from app.eu_market.connectors import jsonld  # noqa: E402
from app.eu_market.connectors.base import ConnectorError  # noqa: E402

# --- registry + status vocabulary (spec §6.2)
cat = {c["source"]: c for c in connectors.catalogue()}
assert cat["jsonld"]["status"] == "available" and cat["jsonld"]["configured"] and cat["jsonld"]["public_data"] and not cat["jsonld"]["env_vars"]
assert cat["kaufland"]["optional_seller"] and cat["allegro"]["status"] == "available" and cat["allegro"]["capabilities"] == ["import"] and cat["merkandi"]["status"] == "unsupported_capability"
assert "ebay" not in cat and "amazon_eu" not in cat and "amazon" not in cat  # removed sources stay removed
assert list(cat)[0] == "jsonld"  # public-data-first ordering
assert "jsonld" not in connectors.searchable()  # no search capability → never part of keyword sync

# --- JSON-LD parser
PAGE = """<html><head><script type="application/ld+json">
{"@context":"https://schema.org/","@type":"Product","name":"Glücksarmband Pixiu Obsidian","image":["https://shop.de/img/pixiu-1.jpg","https://shop.de/img/pixiu-2.jpg"],
 "sku":"PX-001","gtin13":"4010884503135","brand":{"@type":"Brand","name":"Lucky Co"},"category":"Schmuck > Armbänder",
 "offers":{"@type":"Offer","url":"https://shop.de/p/pixiu","priceCurrency":"EUR","price":"19,90","availability":"https://schema.org/InStock",
           "seller":{"@type":"Organization","name":"Lucky Shop"},"priceSpecification":{"@type":"PriceSpecification","price":19.9,"priceCurrency":"EUR","valueAddedTaxIncluded":true}}}
</script></head><body>…</body></html>"""
it = jsonld.parse_product(PAGE, "https://shop.de/p/pixiu?utm=x")
assert it["source"] == "jsonld" and it["source_product_id"] == "shop.de:PX-001" and it["market_country"] == "DE"
assert it["title"] == "Glücksarmband Pixiu Obsidian" and it["price"] == 19.9 and it["currency"] == "EUR" and it["price_includes_vat"] is True
assert it["thumbnail_url"] == "https://shop.de/img/pixiu-1.jpg" and it["ean"] == "4010884503135" and it["brand"] == "Lucky Co"
assert it["seller_name"] == "Lucky Shop" and it["availability"] == "in_stock" and it["product_url"] == "https://shop.de/p/pixiu"
assert it["data_access"] == "public_page_jsonld" and it["confidence"] == 0.8 and it["raw"]["page"].endswith("utm=x")

GRAPH = """<script type='application/ld+json'>{"@context":"https://schema.org","@graph":[{"@type":"WebPage","name":"x"},
 {"@type":["Product"],"name":"Bracelet porte-bonheur","image":{"@type":"ImageObject","url":"https://boutique.fr/a.png"},
  "offers":{"@type":"AggregateOffer","lowPrice":"9.5","highPrice":"14","priceCurrency":"EUR","offerCount":3,"availability":"http://schema.org/OutOfStock"}}]}</script>"""
g = jsonld.parse_product(GRAPH, "https://boutique.fr/produit/1")
assert g["market_country"] == "FR" and g["price"] == 9.5 and g["availability"] == "out_of_stock" and g["thumbnail_url"] == "https://boutique.fr/a.png"
assert g["price_includes_vat"] is None and g["seller_name"] == "boutique.fr" and len(g["source_product_id"]) == 24  # no sku → url hash
assert jsonld.parse_product("<html>no ld</html>", "https://x.com/") is None
assert jsonld.parse_product('<script type="application/ld+json">{"@type":"Product","name":"no offer"}</script>', "https://x.pl/p")["price"] is None
assert jsonld.country_of("https://www.amazon.co.uk/dp/x", None) == "GB" and jsonld.country_of("https://shop.com/x", "PLN") == "PL" and jsonld.country_of("https://shop.com/x", "EUR") == "EU"


# price meta-tag fallback (Shopify / Woo style, no Product JSON-LD)
META = """<head><meta property="og:title" content="Pixiu Armband Obsidian"><meta property="og:image" content="https://shop.fr/i.jpg">
<meta property="product:price:amount" content="24,90"><meta property="product:price:currency" content="EUR">
<meta property="og:availability" content="in stock"><meta property="og:site_name" content="Boutique FR"></head>"""
m = jsonld.parse_meta(META, "https://boutique.fr/produit/9")
assert m["price"] == 24.9 and m["currency"] == "EUR" and m["market_country"] == "FR" and m["availability"] == "in_stock" and m["data_access"] == "public_page_meta"
assert m["seller_name"] == "Boutique FR" and m["thumbnail_url"] == "https://shop.fr/i.jpg"
assert jsonld.parse_meta('<meta property="og:title" content="Only a title">', "https://x.de/p") is None  # no price → nothing invented
assert jsonld.parse_meta('<meta content="9.99" property="og:price:amount"><meta content="T" property="og:title">', "https://x.de/p")["price"] == 9.99  # attribute order

# robots.txt: Disallow for * blocks; other agents' rules don't
class RC:
    def __init__(self, text, status=200):
        self.text, self.status_code = text, status

    def get(self, url, timeout=0):
        return self


assert jsonld.robots_allows(RC("User-agent: *\nDisallow: /private/\n"), "https://s.de/p/1") is True
assert jsonld.robots_allows(RC("User-agent: *\nDisallow: /p/\n"), "https://s.de/p/1") is False
assert jsonld.robots_allows(RC("User-agent: *\nDisallow: /\n"), "https://s.de/p/1") is False
assert jsonld.robots_allows(RC("User-agent: Googlebot\nDisallow: /\n"), "https://s.de/p/1") is True
assert jsonld.robots_allows(RC("", 404), "https://s.de/p/1") is True
for bad in ("ftp://x/", "http://127.0.0.1/p", "http://localhost/p"):
    try:
        jsonld.fetch_product(bad)
        raise AssertionError(bad)
    except ConnectorError as e:
        assert e.code in ("bad_response", "access_restricted"), (bad, e.code)
print("eu public sources: offline checks passed (registry, jsonld parser, robots, ssrf)")
