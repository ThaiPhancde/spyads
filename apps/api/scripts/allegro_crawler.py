"""Allegro.pl test crawler -> apps/api/data/imports/allegro.json (read by app/eu_market/allegro_import.py).

Methods (picked automatically, in this order):
  1. Official Allegro REST API, only if ALLEGRO_CLIENT_ID + ALLEGRO_CLIENT_SECRET exist (env or D:\\ToolSpy\\.env).
     client_credentials token -> GET https://api.allegro.pl/offers/listing?phrase=...
     NOTE: /offers/listing is only open to apps Allegro has verified for it; an unverified app gets 403.
  2. Public listing page via requests + BeautifulSoup (low CPU, no browser):
     GET https://allegro.pl/listing?string=<keyword>&p=<page>, parse embedded JSON + <article> fallback.

Rules this script follows on purpose:
  * NO CAPTCHA / anti-bot bypass (Allegro uses DataDome). A 403/429/captcha/challenge page => stop, exit code 2.
    No stealth plugins, proxies, stolen cookies or fingerprint spoofing.
  * NO fake / sample data, ever. Only images' URLs are stored; images/videos are never downloaded.
  * Output policy when 0 products are fetched: allegro.json is NOT overwritten (the last real data, if any, stays
    intact) and a run report is written to allegro.last_run.json (status, error, counts) for diagnosis.

Usage (from D:\\ToolSpy):
  apps\\api\\.venv\\Scripts\\python.exe apps\\api\\scripts\\allegro_crawler.py --keyword "bransoletka damska" --limit 20
Exit codes: 4 = disabled (default, see main()), 0 = got >= limit, 1 = partial (some but < limit, file still written), 2 = blocked by anti-bot,
            3 = other error / 0 products.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

import requests
from bs4 import BeautifulSoup

API_DIR = Path(__file__).resolve().parents[1]          # apps/api
ROOT_DIR = API_DIR.parents[1]                           # D:\ToolSpy
OUT_DEFAULT = API_DIR / "data" / "imports" / "allegro.json"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/129.0.0.0 Safari/537.36")
OFFER_RE = re.compile(r"allegro\.pl/oferta/[^\s\"'?#]*?-(\d{8,})")
log = logging.getLogger("allegro")


class Blocked(Exception):
    """Anti-bot / captcha / access-denied response. Never retried or circumvented."""


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_env() -> dict:
    """ALLEGRO_* from process env or ToolSpy .env files (values are never logged)."""
    env = {}
    for p in (ROOT_DIR / ".env", API_DIR / ".env"):
        if p.exists():
            for line in p.read_text(encoding="utf-8-sig", errors="ignore").splitlines():
                m = re.match(r"\s*(ALLEGRO_[A-Z_]+)\s*=\s*(.*)\s*$", line)
                if m:
                    env[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    for k, v in os.environ.items():
        if k.startswith("ALLEGRO_"):
            env[k] = v
    return env


# ---------------------------------------------------------------- record helpers
def clean_url(u: str | None) -> str | None:
    if not u or not isinstance(u, str):
        return None
    u = u.strip()
    if u.startswith("//"):
        u = "https:" + u
    if "allegro.pl/events/clicks" in u or "redirect=" in u:   # sponsored tracking link -> real offer URL
        q = parse_qs(urlparse(u).query)
        if q.get("redirect"):
            u = unquote(q["redirect"][0])
    return u.split("#")[0] if u.startswith("http") else None


def to_price(v) -> float | None:
    if isinstance(v, dict):
        for k in ("amount", "value", "price"):
            if k in v:
                return to_price(v[k])
        return None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v) if v > 0 else None
    if isinstance(v, str):
        s = re.sub(r"[^\d,.]", "", v.replace("\xa0", ""))
        if "," in s and "." in s:
            s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
        else:
            s = s.replace(",", ".")
        try:
            f = float(s)
            return f if f > 0 else None
        except ValueError:
            return None
    return None


def make_record(offer_id, title, price, currency, image_url, product_url, seller) -> dict | None:
    offer_id = str(offer_id or "").strip()
    title = (title or "").strip() if isinstance(title, str) else ""
    if not offer_id.isdigit() or not title:
        return None
    return {"offer_id": offer_id, "title": title, "price": price, "currency": (currency or "PLN").upper(),
            "image_url": clean_url(image_url), "product_url": clean_url(product_url) or f"https://allegro.pl/oferta/{offer_id}",
            "seller": seller or None, "source": "allegro", "fetched_at": now_iso()}


# ---------------------------------------------------------------- official API
def fetch_api(session: requests.Session, env: dict, keyword: str, limit: int) -> list[dict]:
    log.info("Method: official Allegro REST API (client_credentials)")
    r = session.post("https://allegro.pl/auth/oauth/token", data={"grant_type": "client_credentials"},
                     auth=(env["ALLEGRO_CLIENT_ID"], env["ALLEGRO_CLIENT_SECRET"]), timeout=20)
    if r.status_code != 200:
        raise RuntimeError(f"OAuth token failed: HTTP {r.status_code} {r.text[:200]}")
    token = r.json()["access_token"]
    r = session.get("https://api.allegro.pl/offers/listing",
                    params={"phrase": keyword, "limit": min(max(limit, 1), 100)},
                    headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.allegro.public.v1+json"},
                    timeout=20)
    if r.status_code == 403:
        raise RuntimeError("API /offers/listing HTTP 403 - app is not verified for this endpoint "
                           "(apply via developer.allegro.pl).")
    if r.status_code != 200:
        raise RuntimeError(f"API /offers/listing HTTP {r.status_code}: {r.text[:300]}")
    items = r.json().get("items", {})
    out = []
    for o in (items.get("promoted", []) + items.get("regular", [])):
        price = (o.get("sellingMode") or {}).get("price") or {}
        imgs = o.get("images") or []
        rec = make_record(o.get("id"), o.get("name"), to_price(price.get("amount")), price.get("currency"),
                          imgs[0].get("url") if imgs else None, f"https://allegro.pl/oferta/{o.get('id')}",
                          (o.get("seller") or {}).get("login") or (o.get("seller") or {}).get("id"))
        if rec:
            out.append(rec)
    return out


# ---------------------------------------------------------------- public listing page
def check_block(r: requests.Response) -> None:
    body = r.text[:20000].lower() if r.text else ""
    markers = ("captcha-delivery.com", "geo.captcha-delivery", "datadome captcha", "please enable js and disable",
               "interstitial", "/captcha/", "g-recaptcha", "hcaptcha")
    hit = next((m for m in markers if m in body), None)
    if r.status_code in (401, 403, 405, 429) or hit:
        dd = r.headers.get("x-datadome") or r.headers.get("X-DataDome")
        raise Blocked(f"HTTP {r.status_code}" + (f", marker '{hit}'" if hit else "") +
                      (f", x-datadome: {dd}" if dd else "") + f", {len(r.content)} bytes, url={r.url}")


def _walk(o):
    if isinstance(o, dict):
        yield o
        for v in o.values():
            yield from _walk(v)
    elif isinstance(o, list):
        for v in o:
            yield from _walk(v)


def _img_from(d: dict) -> str | None:
    for k in ("mainThumbnail", "thumbnail", "image", "imageUrl"):
        v = d.get(k)
        if isinstance(v, str) and v.startswith(("http", "//")):
            return v
        if isinstance(v, dict):
            for kk in ("url", "medium", "large", "original", "small"):
                if isinstance(v.get(kk), str):
                    return v[kk]
    for k in ("photos", "images", "gallery"):
        v = d.get(k)
        if isinstance(v, list) and v:
            p = v[0]
            if isinstance(p, str):
                return p
            if isinstance(p, dict):
                for kk in ("medium", "large", "url", "original", "small"):
                    if isinstance(p.get(kk), str):
                        return p[kk]
    return None


def _price_from(d: dict):
    p = d.get("price")
    if isinstance(p, dict):
        for k in ("mainPrice", "normal", "buyNow", "price"):
            if isinstance(p.get(k), dict) and "amount" in p[k]:
                return to_price(p[k]["amount"]), p[k].get("currency")
        if "amount" in p:
            return to_price(p["amount"]), p.get("currency")
    if p is not None and not isinstance(p, dict):
        return to_price(p), d.get("currency")
    sm = d.get("sellingMode")
    if isinstance(sm, dict) and isinstance(sm.get("price"), dict):
        return to_price(sm["price"].get("amount")), sm["price"].get("currency")
    return None, None


def _seller_from(d: dict) -> str | None:
    s = d.get("seller")
    if isinstance(s, dict):
        return s.get("login") or s.get("name") or s.get("title")
    if isinstance(s, str):
        return s
    return None


def parse_embedded_json(soup: BeautifulSoup) -> list[dict]:
    """Allegro ships listing data as JSON inside <script type="application/json"> (data-serialize-box-id)."""
    out = []
    for sc in soup.find_all("script"):
        txt = sc.string or sc.get_text() or ""
        if "oferta" not in txt and "offerId" not in txt:
            continue
        txt = txt.strip()
        if not txt.startswith(("{", "[")):
            continue
        try:
            data = json.loads(txt)
        except ValueError:
            continue
        for d in _walk(data):
            url = d.get("url") if isinstance(d.get("url"), str) else None
            title = d.get("name") or d.get("title")
            if isinstance(title, dict):
                title = title.get("text")
            oid = d.get("id") or d.get("offerId")
            if url and (m := OFFER_RE.search(clean_url(url) or "")):
                oid = oid if str(oid or "").isdigit() else m.group(1)
            elif not (url and "allegro.pl" in url and str(oid or "").isdigit()):
                continue
            if not isinstance(title, str):
                continue
            price, cur = _price_from(d)
            rec = make_record(oid, title, price, cur, _img_from(d), url, _seller_from(d))
            if rec:
                out.append(rec)
    return out


def parse_articles(soup: BeautifulSoup) -> list[dict]:
    """Fallback: server-rendered <article> cards with /oferta/...-<id> links."""
    out = []
    for art in soup.find_all("article"):
        a = next((x for x in art.find_all("a", href=True) if OFFER_RE.search(clean_url(x["href"]) or "")), None)
        if not a:
            continue
        url = clean_url(a["href"])
        oid = OFFER_RE.search(url).group(1)
        h = art.find(["h2", "h3"])
        title = (h.get_text(" ", strip=True) if h else a.get("title") or a.get_text(" ", strip=True))
        price_el = art.find(attrs={"aria-label": re.compile(r"cena", re.I)}) or \
            art.find(string=re.compile(r"\d[\d\s]*,\d{2}\s*zł"))
        ptxt = price_el.get("aria-label") if hasattr(price_el, "get") else str(price_el or "")
        img = art.find("img")
        img_url = (img.get("src") or img.get("data-src")) if img else None
        if img_url and img_url.startswith("data:"):
            img_url = img.get("data-src")
        rec = make_record(oid, title, to_price(ptxt) if ptxt else None, "PLN", img_url, url, None)
        if rec:
            out.append(rec)
    return out


def fetch_listing(session: requests.Session, keyword: str, limit: int, max_pages: int, delay: float) -> list[dict]:
    log.info("Method: public listing page (requests + BeautifulSoup)")
    found: dict[str, dict] = {}
    for page in range(1, max_pages + 1):
        url = f"https://allegro.pl/listing?string={quote(keyword)}" + (f"&p={page}" if page > 1 else "")
        log.info("GET %s", url)
        try:
            r = session.get(url, timeout=(10, 25))
        except (requests.Timeout, requests.ConnectionError) as e:
            # Allegro/DataDome is known to stall or drop non-browser TLS clients instead of answering.
            # Treated as a block: no retry, no workaround.
            raise Blocked(f"no response from allegro.pl ({type(e).__name__}) - connection stalled/dropped; "
                          f"likely a silent anti-bot block (or a network problem), url={url}") from None
        log.info("-> HTTP %s, %d bytes", r.status_code, len(r.content))
        check_block(r)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        recs = parse_embedded_json(soup)
        src = "embedded JSON"
        if not recs:
            recs, src = parse_articles(soup), "<article> fallback"
        new = 0
        for rec in recs:   # merge: keep first, fill missing fields from later duplicates
            cur = found.get(rec["offer_id"])
            if cur is None:
                found[rec["offer_id"]] = rec
                new += 1
            else:
                for k, v in rec.items():
                    if cur.get(k) in (None, "") and v not in (None, ""):
                        cur[k] = v
        log.info("page %d: %d parsed via %s, %d new (total %d)", page, len(recs), src, new, len(found))
        if len(found) >= limit or new == 0:
            break
        time.sleep(delay)
    return list(found.values())


# ---------------------------------------------------------------- main
def main() -> int:
    # Paused 2026-10-11: allegro.pl answers this HTTP crawler with DataDome 403. Allegro search now goes through the Grok Bot
    # browser routine (app/eu_market/allegro_bot.py). Kept for reference; set ALLEGRO_HTTP_CRAWLER_ENABLED=1 to run it anyway.
    if os.getenv("ALLEGRO_HTTP_CRAWLER_ENABLED", "0") != "1":
        print("allegro_crawler is disabled (DataDome 403). Use EU Market → Product Explorer → 'Tìm trên Allegro (Grok Bot)'.")
        return 4
    ap = argparse.ArgumentParser(description="Fetch real Allegro.pl offers (no captcha bypass, no fake data).")
    ap.add_argument("--keyword", "-k", default="bransoletka damska")
    ap.add_argument("--limit", "-n", type=int, default=20)
    ap.add_argument("--out", default=str(OUT_DEFAULT))
    ap.add_argument("--max-pages", type=int, default=3)
    ap.add_argument("--delay", type=float, default=2.0, help="seconds between page requests")
    ap.add_argument("--method", choices=("auto", "api", "html"), default="auto")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    out = Path(args.out)
    report_path = out.with_name(out.stem + ".last_run.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "pl-PL,pl;q=0.9,en;q=0.8",
                      "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"})
    env = load_env()
    has_api = bool(env.get("ALLEGRO_CLIENT_ID") and env.get("ALLEGRO_CLIENT_SECRET"))
    log.info("keyword=%r limit=%d api_credentials=%s", args.keyword, args.limit, "yes" if has_api else "no")

    errors, records, method, code = [], [], None, 3
    try:
        if args.method in ("auto", "api") and has_api:
            try:
                method, records = "api", fetch_api(s, env, args.keyword, args.limit)
            except Exception as e:  # noqa: BLE001
                errors.append(f"api: {e}")
                log.error("API failed: %s", e)
        elif args.method == "api":
            errors.append("api: ALLEGRO_CLIENT_ID / ALLEGRO_CLIENT_SECRET not configured")
        if not records and args.method in ("auto", "html"):
            method, records = "html", fetch_listing(s, args.keyword, args.limit, args.max_pages, args.delay)
    except Blocked as e:
        errors.append(f"blocked by anti-bot: {e}")
        log.error("BLOCKED by Allegro anti-bot protection (DataDome): %s", e)
        log.error("Stopping. Not bypassing. Needed: official API credentials for an Allegro-verified app, "
                  "or Allegro's written permission.")
        code = 2
    except Exception as e:  # noqa: BLE001
        errors.append(f"{type(e).__name__}: {e}")
        log.error("Fetch failed: %s: %s", type(e).__name__, e)

    seen, uniq = set(), []
    for r in records:
        if r["offer_id"] not in seen:
            seen.add(r["offer_id"])
            uniq.append(r)
    uniq = uniq[: args.limit]

    if uniq:
        out.write_text(json.dumps(uniq, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info("Wrote %d products -> %s", len(uniq), out)
        code = 0 if len(uniq) >= args.limit else 1
    else:
        log.warning("0 products fetched - %s left untouched (no fake data written).", out)
    report = {"run_at": now_iso(), "keyword": args.keyword, "limit": args.limit, "method": method,
              "fetched": len(uniq), "output": str(out) if uniq else None, "errors": errors, "exit_code": code}
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"RESULT: fetched {len(uniq)}/{args.limit} via {method or '-'}; errors: {errors or 'none'}")
    return code


if __name__ == "__main__":
    sys.exit(main())
