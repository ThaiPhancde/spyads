"""Content relevance filter: keep product / selling ads, drop short-drama, movie, trailer, streaming-app and podcast ads.

Runs in ingest_ads before anything is stored (raw batch, LLM pass, product cluster, creatives), on metadata only:
caption, headline, advertiser, CTA, landing URL / app id, listed price, OCR text when a creative already has it.
No video download, no model call.

    classify(fields) → ("block" | "review" | "keep", reasons)

block  = entertainment and no selling evidence → not stored; (source, source_ad_id, page_id) kept in content_exclusions
review = one weak entertainment hint, nothing to confirm it → stored as usual + queued in content_exclusions for a human
keep   = everything else. Selling evidence (price, product page, shop CTA, device words like projector) always
         outranks entertainment words: a projector ad that says "watch movies" or a VSL with a "story" stays.

A human decision (`allow` / `block` in content_exclusions) wins over the rules forever, whatever RULES_VERSION says.
"""
from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import parse_qs, urlparse

from sqlalchemy import JSON, DateTime, Integer, String, UniqueConstraint, func, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from ..db import Base

RULES_VERSION = "ent-2026.10.14"


class ContentExclusion(Base):
    """One verdict per source ad. Small on purpose: ids + reason, never the creative."""
    __tablename__ = "content_exclusions"
    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(64))
    source_ad_id: Mapped[str] = mapped_column(String(128), index=True)
    page_id: Mapped[str | None] = mapped_column(String(64), index=True)
    advertiser: Mapped[str | None] = mapped_column(String(255))
    landing_host: Mapped[str | None] = mapped_column(String(255))
    decision: Mapped[str] = mapped_column(String(8), index=True)  # block | review | allow
    reasons: Mapped[list] = mapped_column(JSON, default=list)
    rules_version: Mapped[str] = mapped_column(String(24))
    decided_by: Mapped[str] = mapped_column(String(16), default="rules")  # rules | human | cleanup
    ad_id: Mapped[int | None] = mapped_column(Integer)  # the ads row it pointed to when seen (review queue / cleanup)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    __table_args__ = (UniqueConstraint("source", "source_ad_id"),)


# ------------------------------------------------------------ entertainment signals
# short-drama / streaming apps: their name anywhere (advertiser, copy, link) is enough
DRAMA_APPS = re.compile(  # spaced only where both words together never name a product ("short tv" stand, "free reels")
    r"reel ?short|drama ?box|shortmax|shorttv|flare ?flow|melolo|goodshort|flextv|flick ?(reels|story)|freereels|"
    r"drama ?wave|netshort|mobo ?reels|kalos ?tv|stardust ?tv|minishort|topshort|joyreels|dramaflex|mydrama|"
    r"shortical|playlet|crazy ?maple|story ?matrix|pocket ?fm|kuku ?tv|sereal\+|dramashorts|snackshort|hidrama|kisskiss|"
    r"goodreels|vigloo|playdrama|quicktv|toonshort|netreel|reelstorm|playreel|blinkdrama|h5short|شورت ماكس|الحد الأقصى القصير|短剧|微短剧", re.I)
# streaming / podcast services: only as the landing host (a projector ad may say "Netflix")
STREAM_HOSTS = ("netflix.com", "paramountplus.com", "disneyplus.com", "hbomax.com", "max.com", "primevideo.com", "viu.com",
                "iq.com", "iqiyi.com", "wetv.vip", "youku.com", "vivamax.net", "crunchyroll.com", "hulu.com", "shahid.mbc.net",
                "osnplus.com", "tod.tv", "starzplay.com", "bilibili.tv", "mubi.com", "tubitv.com", "plex.tv")
STREAM_BRANDS = re.compile(r"netflix|hotstar|disney ?\+|prime video|hbo ?max|viu|iqiyi|wetv|vivamax|paramount ?\+|crunchyroll|shahid|osn ?\+", re.I)  # advertiser only
PODCAST_RE = re.compile(r"open\.spotify\.com/(episode|show)|podcasts\.apple\.com|podcasts\.google\.com|anchor\.fm", re.I)
APP_ID_RE = re.compile(r"drama|reel|short.?stor|movie|film|series|cinema|playlet|episode|novel|webtoon", re.I)
ADVERTISER_RE = re.compile(r"drama|dramática|dramatica|\bmovies?\b|\bfilms?\b|\bcinema\b|\bseries\b|\bserye\b|"
                           r"\bpelikula|pel[ií]culas|\bshort ?(tv|video|max)\b|\bhistorias\b|\bstories\b|\bphim\b|\bnovelas?\b|مسلسل|أفلام|افلام|短剧|电影|影视", re.I)
WATCH_CTA = {"WATCH_MORE", "WATCH_VIDEO", "LISTEN_NOW", "LISTEN_MUSIC", "WATCH_NOW", "SEE_MORE_EPISODES", "BUY_TICKETS"}
APP_CTA = {"INSTALL_MOBILE_APP", "INSTALL_APP", "DOWNLOAD", "USE_APP", "PLAY_GAME", "OPEN_LINK"}
# copy, by language family — each family that matches counts once
TEXT_RULES: list[tuple[str, re.Pattern]] = [
    ("en", re.compile(r"short ?dramas?|\bfull (episodes?|series|movie|drama)|watch (the )?(full|whole|entire|all|next) "
                      r"(series|movie|episodes?|drama|story)|continue (to )?watch|keep watching|watch (free|now) (on|in) the app|"
                      r"\bepisodes? ?\d|\bep\.? ?\d+\b|\bseason \d|(official|movie|film|final) trailer|watch the trailer|in (theaters|cinemas)|now streaming|"
                      r"binge[- ]?watch|mini ?series|web ?series|new episodes? (daily|every)|updated daily", re.I)),
    ("trope", re.compile(r"\b(billionaire|ceo|alpha|werewolf|mafia boss|lycan|luna)\b.{0,60}\b(wife|bride|mate|husband|"
                         r"lover|mistress|heir|baby)\b|contract marriage|ex-?husband|ex-?wife|his mistress|callboy|"
                         r"second chance at life|reborn (as|into|in the)|revenge on|she schemed", re.I)),
    ("app_pitch", re.compile(r"(free|trending|new|best|top|addictive|endless) (short |micro|mini ?)?dramas|dramas? (for )?(free|gratis)|"
                             r"no (top-?ups?|paywalls?|vip|recharge|subscription)|\d[\d,.]*\+? (free )?dramas|audio ?series|micro ?dramas?|"
                             r"reel shows?|watch (to )?earn|every episode (is )?(unlocked|free)|free episodes?\b|binge (this|the|it|all|every)\b|watch (it )?(full )?for free|earn (coins|while you watch)", re.I)),
    ("tl", re.compile(r"buong (serye|episode|pelikula)|teleserye|panoorin (ang|na)|manood ng|pinoy drama|kdrama|k-drama", re.I)),
    ("vi", re.compile(r"phim (bộ|ngắn|hay|mới|tình cảm|trung quốc|hàn)|xem phim|trọn bộ|full tập|tập \d+|tập cuối", re.I)),
    ("ar", re.compile(r"مسلسل|الحلقة|حلقة \d|حلقات|فيلم|أفلام|افلام|شاهد (الآن|الحلقة|المسلسل)|مشاهدة مجانية|دراما", re.I)),
    ("zh", re.compile(r"短剧|全集|第\s*\d+\s*集|剧集|电视剧|追剧|大结局|电影|免费看")),
    ("es/pt", re.compile(r"dramas? cortos?|seguir viendo|sigue viendo|capítulos?|episodios?|telenovela|ver (la )?serie|"
                         r"assistir|continuar assistindo|novelas? curtas?", re.I)),
    ("ja/ko", re.compile(r"視聴を続ける|続きを見る|全話|第\d+話|ドラマ|이어보기|드라마|전편|무료 시청")),
    ("de/fr", re.compile(r"weiter ansehen|jetzt (ansehen|streamen)|folge \d|serien? (online )?(anschauen|streamen)|"
                         r"\bépisodes?\b|regarder (la suite|gratuitement)|séries? courtes?", re.I)),
    ("id/ms/th", re.compile(r"drama pendek|nonton (drama|film|gratis)|tonton (drama|percuma)|episod \d|ละคร|ซีรีส์|ดูฟรี|รับชมต่อ|ดูต่อ|หนังสั้น", re.I)),
]

# ------------------------------------------------------------ selling evidence
SHOP_CTA = {"SHOP_NOW", "ORDER_NOW", "BUY_NOW", "GET_OFFER", "ADD_TO_CART", "BUY", "SEE_MENU", "GET_QUOTE"}
SHOP_PATH = re.compile(r"/(products?|p|dp|item|items|collections|cart|checkout|shop|store|offer|sp)/", re.I)
APP_STORES = ("play.google.com", "apps.apple.com", "itunes.apple.com")  # "/store/apps/…" is not a shop
SHOP_HOSTS = re.compile(r"shopee|lazada|tiktok\.shop|shop\.tiktok|amazon\.|noon\.com|aliexpress|temu\.|myshopify|ladipage|ladi\.me|"
                        r"shopbase|youcan\.shop|lightfunnels|zalora|tokopedia|carousell|ebay\.|allegro\.", re.I)
SELL_WORDS = re.compile(
    r"projector|máy chiếu|proyektor|proyector|beamer|tv ?box|android box|smart tv|soundbar|speaker|headphones?|earbuds|screen|"
    r"free (shipping|delivery)|cash on delivery|gratis ongkir|diskon|env[ií]o gratis|descuentos?|ofertas?|contra ?reembolso|"
    r"fri frakt|kostenlose?r? versand|rabatt|livraison gratuite|frete gr[aá]tis|sale|"
    r"cash on delivery|\bcod\b|add to cart|order (now|today)|limited stock|in stock|% ?off|buy \d|"
    r"freeship|giao hàng|đặt hàng|mua ngay|thanh toán khi nhận|libreng (shipping|padala)|"
    r"الدفع عند الاستلام|توصيل مجاني|اطلب الآن|خصم|包邮|下单|货到付款", re.I)


# TikTok Creative Center labels every top ad with an industry (raw_source.industry_key). These are not products a MKT can import and
# sell: drama / novels / games / education & jobs / events / entertainment apps / finance / gig apps / business services / cleaning &
# pest-control services / games-real-estate mix / clinics. Read from the 3 561 top ads on file (data/audit/tt4.py): ≈ 90 % of what sits
# under these labels is not a physical product. Selling evidence in the ad itself (price, shop link, COD / "free shipping") still wins.
NON_PRODUCT_INDUSTRIES = {
    "label_23112000000": "drama", "label_23118000000": "novel", "label_23125000000": "game", "label_23102000000": "education/jobs",
    "label_23116000000": "events", "label_23999000000": "misc services", "label_20101000000": "entertainment app",
    "label_20106000000": "finance/trading", "label_20107000000": "gig/delivery app", "label_24112000000": "business services",
    "label_26105000000": "cleaning services", "label_25100000000": "game/real estate", "label_29100000000": "clinic/health services",
}


def _sell_evidence(copy: str, adv: str, cta: str, host: str, url: str, price: float | None, country: str | None, dest: bool = False) -> list[str]:
    from ..ingest import MESS_CTA, parse_price  # lazy: ingest imports this module

    sell = []
    if cta in SHOP_CTA or cta in MESS_CTA:  # chat-to-order (MKT Mess): drama / streaming apps never ask for a message
        sell.append(f"cta:{cta}")
    if not dest:  # click goes to a drama / streaming / podcast / app page: a price in the copy is story, not an offer
        if price is None and (pp := parse_price(copy, country)):
            price = pp[0]
        if price:
            sell.append("price")
        if not host.endswith(APP_STORES) and (SHOP_PATH.search(urlparse(url or "").path) or SHOP_HOSTS.search(host)):
            sell.append(f"shop_link:{host}")
        if m := SELL_WORDS.search(f"{copy} {adv}"):
            sell.append(f"sell_word:{m.group(0).lower()}")
    return sell


def _name(advertiser: str | None) -> str:
    return (advertiser or "").strip().lower()[:255]


def _host(url: str | None) -> str:
    try:
        return urlparse(url or "").netloc.lower().removeprefix("www.")
    except ValueError:
        return ""


def _app_id(url: str | None) -> str:
    u = urlparse(url or "")
    if "play.google.com" in u.netloc:
        return (parse_qs(u.query).get("id") or [""])[0]
    if "apps.apple.com" in u.netloc or "itunes.apple.com" in u.netloc:
        return u.path
    return ""


def classify(advertiser: str | None = None, title: str | None = None, text: str | None = None, cta_type: str | None = None,
             cta_text: str | None = None, url: str | None = None, price: float | None = None, ocr: str | None = None,
             country: str | None = None, blocked_page: bool = False, industry: str | None = None, network: str | None = None) -> tuple[str, list[str]]:
    adv = advertiser or ""
    copy = " ".join(filter(None, [title, text, cta_text]))
    host, cta = _host(url), (cta_type or "").upper()
    ent, strong, why = 0, False, []

    if network == "tiktok" and not copy.strip() and not url:  # nothing to identify: only an advertiser id (Commercial Content Library rows)
        return "block", ["empty_tiktok_ad"]
    if industry in NON_PRODUCT_INDUSTRIES:
        if not _sell_evidence(copy, adv, cta, host, url or "", price, country):
            return "block", [f"industry:{NON_PRODUCT_INDUSTRIES[industry]}"]
    dest = False  # the landing itself is entertainment (drama app link, streaming / podcast host, entertainment app id)
    for where, s in (("advertiser", adv), ("link", url or ""), ("copy", copy)):
        if m := DRAMA_APPS.search(s):
            strong, dest = True, dest or where == "link"
            why.append(f"drama_app:{where}:{m.group(0).lower()}")
            break
    if re.search(r"drama(?!mine)", host) and not why:  # blinkdrama.life, quickdrama.top … (not dramamine.com)
        strong = dest = True
        why.append(f"drama_host:{host}")
    if any(host == h or host.endswith("." + h) for h in STREAM_HOSTS):
        strong = dest = True
        why.append(f"streaming_host:{host}")
    if STREAM_BRANDS.search(adv):
        strong = True
        why.append(f"streaming_advertiser:{adv[:40]}")
    if PODCAST_RE.search(url or ""):
        strong = dest = True
        why.append("podcast_link")
    if (aid := _app_id(url)) and APP_ID_RE.search(aid):
        strong = dest = True
        why.append(f"entertainment_app:{aid[:60]}")
    if blocked_page:  # weak on purpose: a marketplace page (Shopee) can run one drama promo among thousands of product ads
        ent += 1
        why.append("page_blocked_before")
    if ADVERTISER_RE.search(adv):
        ent += 1
        why.append(f"advertiser_name:{adv[:40]}")
    said = strong
    for lang, rx in TEXT_RULES:
        if m := (rx.search(copy) or (rx.search(ocr) if ocr else None)):
            ent, said = ent + 1, True
            why.append(f"text_{lang}:{m.group(0).lower()[:30]}")
    if said and (cta in WATCH_CTA or cta in APP_CTA):  # "watch more" / install alone: shops use them too
        ent += 1
        why.append(f"cta:{cta}")

    if not (strong or ent):  # most ads: no entertainment hint at all, skip the selling checks
        return "keep", []
    sell = _sell_evidence(copy, adv, cta, host, url or "", price, country, dest)
    if sell:
        # a drama app with a price is a subscription upsell, still not a product — but let a human say so
        return ("review" if strong else "keep"), why + [f"selling:{','.join(sell)}"]
    if strong or ent >= 2:
        return "block", why
    return "review", why


def classify_record(rec, ocr: str | None = None, blocked_page: bool = False) -> tuple[str, list[str]]:
    """AdRecord (collector contract) → verdict."""
    return classify(rec.advertiser, rec.title, rec.ad_text, rec.cta_type, rec.cta_text, rec.landing_page or rec.product_url,
                    rec.price, ocr, rec.country, blocked_page, (rec.raw_source or {}).get("industry_key"), rec.platform)


def screen(db: Session, records: list) -> tuple[list, int]:
    """Drop entertainment AdRecords before ingest stores anything. Returns (kept records, number dropped).
    Marketplace listings (commerce rows) are products by definition and pass untouched."""
    from ..platforms import channel_of, network_of

    cand = [r for r in records if channel_of(network_of(r.platform, r.source)) != "commerce"]
    if not cand:
        return records, 0
    known: dict[tuple[str, str], str] = {}
    for src in {r.source for r in cand}:  # ponytail: one IN() per source, batches are a few hundred ids
        ids = [r.source_ad_id for r in cand if r.source == src]
        known |= {(src, i): d for i, d in db.execute(select(ContentExclusion.source_ad_id, ContentExclusion.decision)
                                                     .where(ContentExclusion.source == src, ContentExclusion.source_ad_id.in_(ids)))}
    # a page / advertiser that already ran a blocked ad is an entertainment page (TikTok rows often have no page_id)
    pages = {r.page_id for r in cand if r.page_id}
    names = {_name(r.advertiser) for r in cand if _name(r.advertiser)}
    blocked = ContentExclusion.decision == "block"
    bad_pages = set(db.scalars(select(ContentExclusion.page_id).where(ContentExclusion.page_id.in_(pages), blocked).distinct())) if pages else set()
    bad_names = set(db.scalars(select(func.lower(ContentExclusion.advertiser)).where(
        func.lower(ContentExclusion.advertiser).in_(names), blocked).distinct())) if names else set()

    drop: set[int] = set()
    for r in cand:
        prior = known.get((r.source, r.source_ad_id))
        if prior == "block":
            drop.add(id(r))
            continue
        if prior:  # allow / review: decided (or queued) already
            continue
        verdict, why = classify_record(r, blocked_page=(r.page_id in bad_pages if r.page_id else False) or _name(r.advertiser) in bad_names)
        if verdict == "keep":
            continue
        db.add(ContentExclusion(source=r.source, source_ad_id=r.source_ad_id, page_id=r.page_id, advertiser=_name(r.advertiser) or None,
                                landing_host=_host(r.landing_page)[:255] or None, decision=verdict, reasons=why,
                                rules_version=RULES_VERSION))
        known[(r.source, r.source_ad_id)] = verdict
        if verdict == "block":
            drop.add(id(r))
    db.flush()
    return [r for r in records if id(r) not in drop], len(drop)
