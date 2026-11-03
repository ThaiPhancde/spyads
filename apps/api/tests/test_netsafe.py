"""SSRF guard (netsafe.safe_get) on every hop + FX: an unknown currency never converts ×1."""
import socket

import httpx
import pytest

from app import netsafe
from app.netsafe import FetchError, safe_get
from app.services.enrichment import price_segment, to_usd

DNS = {"shop.example": "93.184.216.34", "cdn.example": "151.101.1.1", "internal.example": "10.0.0.5"}


@pytest.fixture
def net(monkeypatch):
    """Fake resolver + httpx MockTransport; returns the list of URLs actually requested."""
    seen: list[str] = []

    def getaddrinfo(host, *_a, **_k):
        ip = DNS.get(host, host)
        try:
            socket.inet_pton(socket.AF_INET6 if ":" in ip else socket.AF_INET, ip)
        except OSError:
            raise socket.gaierror(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]

    def handler(req: httpx.Request):
        seen.append(str(req.url))
        if req.url.host == "shop.example" and req.url.path == "/go-internal":
            return httpx.Response(302, headers={"location": "http://internal.example/admin"})
        if req.url.host == "shop.example" and req.url.path == "/go-cdn":
            return httpx.Response(302, headers={"location": "https://cdn.example/a.jpg"})
        return httpx.Response(200, content=b"ok-body", headers={"content-type": "text/html; charset=utf-8"})

    real = httpx.Client
    monkeypatch.setattr(netsafe.socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(netsafe.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    return seen


@pytest.mark.parametrize("url", ["http://127.0.0.1/", "http://10.1.2.3/x", "http://169.254.169.254/latest/meta-data/",
                                 "http://[::1]/", "ftp://shop.example/a", "file:///etc/passwd"])
def test_blocks_private_and_bad_scheme(net, url):
    with pytest.raises(FetchError) as e:
        safe_get(url, max_bytes=1000, timeout=5)
    assert e.value.code in ("url_private_address", "url_invalid")
    assert net == []  # nothing was requested


def test_redirect_to_private_is_never_requested(net):
    with pytest.raises(FetchError) as e:
        safe_get("http://shop.example/go-internal", max_bytes=1000, timeout=5)
    assert e.value.code == "url_private_address"
    assert net == ["http://shop.example/go-internal"]


def test_public_redirect_followed_and_byte_cap(net):
    r = safe_get("http://shop.example/go-cdn", max_bytes=1000, timeout=5)
    assert (r.status_code, r.content, r.text, r.url) == (200, b"ok-body", "ok-body", "https://cdn.example/a.jpg")
    with pytest.raises(FetchError) as e:
        safe_get("http://shop.example/page", max_bytes=3, timeout=5)
    assert e.value.code == "too_large"


def test_impersonated_path_checks_every_hop(net, monkeypatch):
    """curl_cffi (spy.fetch_landing) follows redirects by hand too: a 302 to the metadata IP is refused before the hop."""
    from curl_cffi import requests as cr

    class Resp:
        status_code, headers = 302, {"location": "http://169.254.169.254/latest/"}

        def close(self):
            pass

    class Sess:
        def __init__(self, **kw):
            pass

        __enter__ = lambda self: self
        __exit__ = lambda self, *a: None

        def get(self, url, **kw):
            assert kw["allow_redirects"] is False
            net.append(url)
            return Resp()

    monkeypatch.setattr(cr, "Session", Sess)
    with pytest.raises(FetchError) as e:
        safe_get("https://shop.example/products/x", max_bytes=1000, timeout=5, impersonate="chrome")
    assert e.value.code == "url_private_address"
    assert net == ["https://shop.example/products/x"]


def test_unknown_currency_is_none_not_x1():
    assert to_usd(100, "XYZ") is None and to_usd(3000, "JPY") is None
    assert price_segment(3000, "JPY") is None
    assert to_usd(10, "AUD") == pytest.approx(6.5) and to_usd(5, None) == 5


def test_ecb_rates_fill_currencies_the_static_table_lacks(monkeypatch):
    from app.eu_market import service as eu
    from app.services import enrichment

    monkeypatch.setattr(eu, "fx_table", lambda db: {"EUR": (1.0, None), "USD": (1.1, "2026-10-22"), "JPY": (176.0, "2026-10-22")})
    monkeypatch.setattr(enrichment, "_LIVE_USD", {})
    assert enrichment.refresh_rates(None) == 3
    assert to_usd(1760, "JPY") == pytest.approx(11.0)  # 1760 / 176 EUR × 1.1
    assert to_usd(100, "SAR") == pytest.approx(27)     # not an ECB currency: static rate
    assert to_usd(100, "XYZ") is None
