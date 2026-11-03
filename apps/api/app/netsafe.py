"""SSRF-safe GET for URLs that come from third parties (landing pages, CDN media, product pages, pictures).

http(s) only; every hop's host must resolve to public addresses only — checked BEFORE that hop is requested, redirects
are followed by hand. ponytail: DNS is resolved twice (check + client) — a rebinding race is theoretical here; pin the
IP via a custom transport if this ever faces the open internet.
"""
from __future__ import annotations

import ipaddress
import json
import socket
from contextlib import contextmanager
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import httpx

REDIRECTS = (301, 302, 303, 307, 308)


class FetchError(ValueError):
    """code = stable machine-readable reason (url_invalid / url_unresolvable / url_private_address / ...)."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(detail or code)
        self.code = code


def _is_private(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified


def check_public_host(host: str):
    """Every resolved address must be public (no internal / private targets)."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        raise FetchError("url_unresolvable", host)
    for *_, sa in infos:
        if _is_private(ipaddress.ip_address(sa[0])):
            raise FetchError("url_private_address", host)


def check_url(url: str):
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise FetchError("url_invalid", url[:120])
    check_public_host(u.hostname)


def read_capped(chunks, max_bytes: int) -> bytes:
    buf = bytearray()
    for chunk in chunks:
        buf += chunk
        if len(buf) > max_bytes:
            raise FetchError("too_large", f"> {max_bytes // 1048576} MB")
    return bytes(buf)


@contextmanager
def safe_stream(url: str, *, timeout: float, headers: dict | None = None, max_redirects: int = 4):
    """Streamed httpx GET of the final hop (status not checked; caller reads with read_capped / iter_bytes)."""
    with httpx.Client(follow_redirects=False, timeout=timeout, headers=headers) as c:
        for _ in range(max_redirects + 1):
            check_url(url)
            with c.stream("GET", url) as r:
                if r.status_code in REDIRECTS and r.headers.get("location"):
                    url = str(httpx.URL(url).join(r.headers["location"]))
                    continue
                yield r
                return
    raise FetchError("url_too_many_redirects")


@dataclass
class Fetched:
    status_code: int
    headers: httpx.Headers
    content: bytes
    url: str  # final hop

    @property
    def text(self) -> str:
        m = self.headers.get("content-type", "")
        cs = m.split("charset=", 1)[1].split(";")[0].strip(' "') if "charset=" in m else "utf-8"
        try:
            return self.content.decode(cs, "replace")
        except LookupError:
            return self.content.decode("utf-8", "replace")

    def json(self):
        return json.loads(self.content)


def safe_get(url: str, *, max_bytes: int, timeout: float, headers: dict | None = None, impersonate: str | None = None,
             max_redirects: int = 4) -> Fetched:
    """Whole body (≤ max_bytes, else FetchError too_large). impersonate="chrome" → curl_cffi (browser TLS fingerprint,
    gets past Shopify's bot wall) with the same per-hop check; network errors are the client's own exceptions."""
    if not impersonate:
        with safe_stream(url, timeout=timeout, headers=headers, max_redirects=max_redirects) as r:
            return Fetched(r.status_code, r.headers, read_capped(r.iter_bytes(65536), max_bytes), str(r.url))
    from curl_cffi import requests as cr  # dependency of meta-ads-collector

    with cr.Session(impersonate=impersonate) as s:  # one session: cookies set on a redirect hop survive
        for _ in range(max_redirects + 1):
            check_url(url)
            r = s.get(url, headers=headers, timeout=timeout, allow_redirects=False, stream=True)
            try:
                if r.status_code in REDIRECTS and r.headers.get("location"):
                    url = urljoin(url, r.headers["location"])
                    continue
                return Fetched(r.status_code, httpx.Headers(list(r.headers.multi_items())), read_capped(r.iter_content(), max_bytes), url)
            finally:
                r.close()
    raise FetchError("url_too_many_redirects")
