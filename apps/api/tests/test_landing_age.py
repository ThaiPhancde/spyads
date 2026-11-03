"""Landing page age (design §2.3): platform from HTML markers, Shopify .json published_at, 14-day red flag."""
from datetime import datetime
from types import SimpleNamespace

import pytest

from app.services import spy


@pytest.mark.parametrize("html,platform", [
    ('<link href="//cdn.shopify.com/s/files/1/theme.css">', "shopify"),
    ("<script>Shopify.theme = {\"name\":\"Dawn\"};</script>", "shopify"),
    ('<script src="https://cdn.shoplazza.com/x.js"></script>', "shoplazza"),
    ('<img src="https://w.ladicdn.com/a.png">', "ladipage"),
    ('<link href="/wp-content/plugins/woocommerce/style.css">', "woocommerce"),
    ('<link href="/wp-content/themes/blog.css">', "unknown"),  # WordPress without WooCommerce is no store
    ("<html><body>hi</body></html>", "unknown"),
])
def test_detect_platform(html, platform):
    assert spy.detect_platform(html) == platform


def test_shopify_published_at():
    assert spy.shopify_published_at({"published_at": "2026-09-01T10:00:00-05:00", "created_at": "2026-01-01T00:00:00Z"}) == datetime(2026, 9, 1, 15, 0)
    assert spy.shopify_published_at({"published_at": None, "created_at": "2026-01-01T00:00:00Z"}) == datetime(2026, 1, 1)
    assert spy.shopify_published_at({}) is None


def test_page_age_flag():
    video = datetime(2026, 10, 20)
    assert spy.page_age_flag(SimpleNamespace(page_published_at=datetime(2026, 10, 1)), video) == {"days_older": 19, "flag": True}
    assert spy.page_age_flag(SimpleNamespace(page_published_at=datetime(2026, 10, 6)), video) == {"days_older": 14, "flag": False}
    assert spy.page_age_flag(SimpleNamespace(page_published_at=None), video) == {"days_older": None, "flag": False}


def test_fetch_landing_reads_shopify_json_even_when_priced(monkeypatch):
    page = '<link href="//cdn.shopify.com/t.css"><meta property="og:price:amount" content="29.99">'
    calls = []

    def fake_get(url):
        calls.append(url)
        if url.endswith(".json"):
            return SimpleNamespace(status_code=200, url=url, headers={"content-type": "application/json"},
                                   json=lambda: {"product": {"published_at": "2026-08-01T00:00:00Z", "variants": [{"price": "19.00"}]}})
        return SimpleNamespace(status_code=200, url=url, text=page, headers={"content-type": "text/html"})

    monkeypatch.setattr(spy, "_get", fake_get)
    out = spy.fetch_landing("https://shop.example/products/lamp?variant=1")
    assert calls == ["https://shop.example/products/lamp?variant=1", "https://shop.example/products/lamp.json"]
    assert (out["page_platform"], out["page_published_at"], out["price"]) == ("shopify", datetime(2026, 8, 1), 29.99)


def test_shoplazza_date_comes_from_this_products_embedded_json():
    from app.services.spy import detect_platform, shoplazza_published_at

    html = ('<img src="//img.staticdj.com/a.jpeg">'
            '{"id":"cdf25657-cf36-487a-a9c9-1491550961ec","handle":"warm-long-boots","published":true,'
            '"updated_at":"2026-10-09T03:37:51Z","created_at":"2025-10-16T05:56:03Z"}'
            '{"url":"\/products\/warm-long-boots","published_at":"2025-10-16T05:56:52Z","sales":655}'
            '{"url":"\/products\/other-bag","published_at":"2024-01-01T00:00:00Z"}')  # a recommended product: ignored
    assert detect_platform(html) == "shoplazza"  # Shoplazza CDN, no "shoplazza" text needed
    assert shoplazza_published_at(html, "warm-long-boots") == datetime(2025, 10, 16, 5, 56, 3)
    assert shoplazza_published_at(html, "missing-handle") is None
