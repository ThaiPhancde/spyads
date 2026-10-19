"""Product naming from evidence (docs/PRODUCT_NAMING.md).

One ad → ranked name candidates, best evidence first:
  listing   marketplace listing title (AliExpress / 1688 / Taobao row: the title IS the product)
  slug      product-page URL slug (/products/<name>, Amazon /<name>/dp/, Shopee <name>-i.<shop>.<item>)
  adapter   product name the source itself reported (Meta link title, generic import) — not a search keyword
  ad_title  headline / caption with CTA, prices, hashtags, emoji and pronoun sentences removed
  llm       optional model pass (enrichment.extract_products), kept only when grounded in the ad's own words

Never a name: the advertiser / shop, a platform ("TikTok Shop", "Shopee"), a CTA ("Order Now"), a promo line.
No evidence → None (status UNKNOWN); nothing is invented.

Confidence per source is the precision measured on a hand-labelled sample (scripts/eval_naming.py), not a model's
self-reported probability. Re-run the eval and update SOURCE_PRECISION when the rules change.
"""
from __future__ import annotations

import re
from urllib.parse import unquote, urlparse

from . import lexicon

# precision of "the name is the product sold", hand-labelled live ads (scripts/naming_labels.json, 2026-10-13):
# listing 40/40 · slug 72/80 · ad_title 26/40. adapter runs the same copy rules as ad_title (not sampled separately);
# llm is set to ad_title's until it has its own labelled sample — both stay SUGGESTED unless a second source agrees.
# legacy = a name from before this module that holds a product noun: 24/40 (without a noun: 10/40 → re-derived).
SOURCE_PRECISION = {"manual": 1.0, "listing": 1.0, "slug": 0.9, "adapter": 0.65, "ad_title": 0.65, "llm": 0.65, "legacy": 0.6}
AUTO_CONFIRM_AT = 0.85  # sources at or above → AUTO_CONFIRMED; below → SUGGESTED (MKT confirms)
STATUS_RANK = {"UNKNOWN": 0, "SUGGESTED": 1, "AUTO_CONFIRMED": 2, "VERIFIED": 3}
UNKNOWN_LABEL = "Chưa xác định sản phẩm"

PLATFORMS = re.compile(
    r"^(tik ?tok( shop)?( by tokopedia)?( for business)?( philippines| ph| vietnam| vn| malaysia| thailand| indonesia)?|shopee( ?food)?( vn| ph)?|lazada|"
    r"temu|shein|alibaba(\.com)?|aliexpress|amazon|noon|ebay|walmart|tokopedia|zalora|carousell|1688|taobao|tmall|"
    r"facebook|instagram|messenger|whatsapp|google|youtube|meta|canvas doc|messenger doc|flash ?sale|warehouse wholesale)$", re.I)
CTA = re.compile(
    r"^(shop|buy|order|click|tap|swipe|get|grab|claim|discover|learn|see|watch|send|message|chat|book|sign|call|try|visit|check|"
    r"add to cart|follow|comment|limited|sale|free|mua|sắm|săn|nhận|đặt|xem|nhắn|inbox|ib|pm|dm|link|bili na|order na|اطلب|تسوق|اشتر)\b"
    r"|% ?off|\bfree ?ship|\bsale\b|\bdeals?\b|\bpromo|\bdiscount|giảm giá|khuyến mãi|ưu đãi|freeship|\bcod\b|\blimited\b|\bbuy \d|"
    r"\btake \d|\bend(s)? soon\b|\bclearance\b|\b(any|\d+) (pieces?|items?)\b|\boff$|خصم|عرض", re.I)
PRONOUN = re.compile(r"(?<!\w)(i|i'm|im|you|you're|your|we|we're|our|my|me|us|they|this is|it's|ako|ikaw|mo|ko|natin|kayo|"
                     r"tôi|bạn|mình|chúng|anh|chị|em)(?!\w)", re.I)
SENTENCE = re.compile(r"(?<!\w)(is|are|was|were|will|can|could|should|would|do|does|did|have|has|had|be|been|get|got|"
                      r"make|makes|need|want|feel|know|why|how|what|when|where|who|reasons?|"
                      r"receive|win|enjoy|experience|turn|discover|"
                      r"là|có|được|không|sẽ|đã|đang|vì|tại sao|muốn|biến|thành|gần|hạn chế|vào|bị|với|những|này|rồi|thì|nhất|hiện nay|"
                      r"ang|ng|sa|na|ay|may|dito|ito|sobrang|"
                      r"مين|من|في|لكل|داخل|بأسعار|يحميك|هل|ce|cette|c'est|est|voici)(?!\w)", re.I)
# Arabic "your / you" suffix = a sentence to the reader (يحميك، براحتك، هيبتك) — not كلاسيك / بلاستيك
AR_YOU = re.compile(r"[؀-ۿ]{2,}(تك|كم)(?!\w)|(?<!\w)ي[؀-ۿ]{2,}يك(?!\w)")
FIRST_WORD = re.compile(r"^(or|and|of|for|with|to|from|at|in|on|by|than|ou|et|de|và|hoặc|cho|của|tại|từ|o|at|و|أو)\s", re.I)
LEAD = re.compile(r"^(introducing|meet|new|the new|all[- ]new|say hello to|check out|discover|try|get|shop|buy|order|grab|"
                  r"our|the|this|your|best|hot|original|authentic|premium|top)\s+", re.I)
LISTING_FILLER = re.compile(
    r"\b(20\d\d|new|hot ?sale|hot ?selling|hot|wholesale|dropshipping|drop shipping|free shipping|high quality|best ?seller|"
    r"creative|fashion|trendy|popular|personality|cross[- ]border|factory( direct)?|in stock|spot|explosive|"
    r"for (men|women) and (women|men)|unisex)\b", re.I)
JUNK = re.compile(r"[₱$€£¥]\s?\d[\d.,]*|\d[\d.,]*\s?(php|sar|aed|usd|kwd|egp|aud|gbp|eur|vnd|đ|k)\b|\d{1,3}\s?%", re.I)
CJK = re.compile(r"[぀-ヿ㐀-鿿]")
SLUG_SKIP = {"products", "product", "p", "collections", "shop", "item", "items", "pages", "dp", "vn", "en", "ar", "sa", "view", "detail"}
MARKETPLACE_HOST = re.compile(r"alibaba|aliexpress|amazon\.|noon\.com|shopee|lazada|temu\.|ebay\.|1688\.com|taobao|tmall|tokopedia", re.I)
SOCIAL_HOST = re.compile(r"(^|\.)(facebook\.com|fb\.com|fb\.me|instagram\.com|tiktok\.com|m\.me|wa\.me|whatsapp\.com|messenger\.com|"
                         r"play\.google\.com|apps\.apple\.com|itunes\.apple\.com|onelink\.me)$", re.I)


def _clean(s: str | None) -> str:
    s = re.sub(r"https?://\S+|[#@][\w.]+", " ", s or "")
    s = JUNK.sub(" ", s)
    s = re.sub(r"[^\w\s&+'./-]|_", " ", s)
    return re.sub(r"\s+", " ", s).strip(" -.,/&+'")


def _same(a: str | None, b: str | None) -> bool:
    n = lambda x: re.sub(r"\W+", "", (x or "").lower())
    return bool(n(a)) and n(a) == n(b)


def is_bad_name(name: str | None, advertiser: str | None = None) -> bool:
    """CTA / promo line / platform / shop name / sentence — not a product name."""
    n = _clean(name)
    if len(n) < 3 or (not CJK.search(n) and len(n) < 4):
        return True
    if advertiser and (_same(n, advertiser) or _same(re.sub(r"\s*(official|store|shop|ph|vn|vietnam|philippines)$", "", n, flags=re.I), advertiser)):
        return True
    return bool(PLATFORMS.match(n) or CTA.search(n) or re.fullmatch(r"[\w.-]+\.(com|net|store|shop|ph|sa|vn|ae)", n.lower()))


def _phrase(s: str, max_words: int = 8, noun: bool = False) -> str | None:
    """First clause that reads like a noun phrase (2-8 words, no pronouns / verbs / CTA)."""
    for seg in re.split(r"[.!?|:;,\n–—()\[\]]+|\s-\s", s or ""):
        seg = _clean(seg)
        for _ in range(3):
            seg = LEAD.sub("", seg)
        seg = re.sub(r"\s+(for|to|with|on|in|at|from)\s+(your|you|my|our|me|us|everyone|all)(?!\w).*$", "", seg, flags=re.I)  # "Pen for your Device"
        m = re.search(r"(?<!\w)with (the |a |an |our )?(.+)$", seg, re.I)
        if m and lexicon.has_product_noun(m.group(2)) and not lexicon.has_product_noun(seg[:m.start()]):
            seg = m.group(2)  # "Go natural with Kasoy Cream" → "Kasoy Cream"
        seg = re.sub(r"\s+(today|now|here|ngay|na)$|\s+(in|on|at|from) (the )?(tik ?tok( shop)?|shopee|lazada|amazon|temu)(?!\w).*$", "", seg, flags=re.I)
        m = SENTENCE.search(seg) or PRONOUN.search(seg)
        head = seg[:m.start()].strip(" -,&") if m else ""
        if len(head.split()) >= 2 and lexicon.has_product_noun(head) and not (CTA.search(head) or FIRST_WORD.match(head)):
            seg = head  # "Fengshui Pixiu Obsidian Bracelet Is Known To…" → the noun phrase before the sentence
        words = seg.split()
        if CJK.search(seg):
            if 2 <= len(seg) <= 40 and not (noun and not lexicon.has_product_noun(seg)):
                return seg
            continue
        if not 2 <= len(words) <= max_words or PRONOUN.search(seg) or SENTENCE.search(seg) or CTA.search(seg) or FIRST_WORD.match(seg) or AR_YOU.search(seg):
            continue
        if sum(w.isalpha() for w in words) < 2 or noun and not lexicon.has_product_noun(seg):
            continue
        return seg
    return None


def listing_name(title: str | None) -> str | None:
    if CJK.search(title or ""):
        t = _clean(title)
        return t[:40] if len(t) >= 2 else None
    t = LISTING_FILLER.sub(" ", _clean(re.split(r"[,|/]| - ", title or "")[0]))
    words = re.sub(r"\s+", " ", t).split()
    return " ".join(words[:8]).strip(" &-+,") if len(words) >= 2 else None


def slug_name(url: str | None) -> str | None:
    if not url:
        return None
    u = urlparse(url)
    host, path = u.netloc.lower().removeprefix("www."), unquote(u.path)
    if SOCIAL_HOST.search(host):
        return None
    if m := re.search(r"/([^/]{8,})/dp/", path):  # amazon.*/<Name>/dp/ASIN
        cand = m.group(1)
    elif m := re.search(r"/([^/]{8,})-i\.\d+\.\d+", path):  # shopee <Name>-i.<shop>.<item>
        cand = m.group(1)
    elif m := re.search(r"/products?/([^/]{6,}?)(-i\d+(-s\d+)?)?(\.html?)?$", path):  # shopify / lazada / woo
        cand = m.group(1)
    elif not MARKETPLACE_HOST.search(host) and re.search(r"/(p|item)/", path):
        cand = next((p for p in reversed(path.split("/")) if p and p.lower() not in SLUG_SKIP and not p.isdigit() and len(p) >= 6), "")
    else:
        return None
    words = [w for w in re.split(r"[-_+\s]+", re.sub(r"\.(html?|php)$", "", cand)) if w and not w.isdigit() and not re.fullmatch(r"[a-z0-9]{10,}", w.lower())]
    return " ".join(words[:8]).title() if len(words) >= 2 else None


def candidates(ad: dict) -> list[tuple[str, str]]:
    """ad: {channel, title, ad_text, landing_url, product_url, advertiser, product_name, matched_query, llm_name}
    → [(name, source)] best evidence first, bad names dropped, duplicates collapsed."""
    adv = ad.get("advertiser")
    out: list[tuple[str, str]] = []
    md = ad.get("metadata") or {}
    if md.get("title"):
        out.append((listing_name(md["title"]), "adapter"))
    if ad.get("channel") == "commerce":
        out.append((listing_name(ad.get("title") or ad.get("product_name")), "listing"))
    for u in (ad.get("product_url"), ad.get("landing_url")):
        out.append((slug_name(u), "slug"))
    pn = ad.get("product_name")
    if pn and not _same(pn, ad.get("matched_query")) and not _same(pn, ad.get("title")) and ad.get("channel") != "commerce":
        out.append((_phrase(pn, 9, noun=True), "adapter"))  # source-reported name (link title) — the keyword we searched is not evidence
    title = ad.get("title")
    service = SERVICE_WORDS.search(" ".join(str(ad.get(k) or "") for k in ("title", "ad_text", "advertiser")))
    if service:  # a spa / clinic / course ad: "Full Body Massage" is what they sell, not a product — only a shop page names one
        out = [c for c in out if c[1] in ("listing", "slug")]
        title = None
    out.append((_phrase(title, noun=True), "ad_title"))  # copy names the product only when it holds a product noun (lexicon.py):
    if ad.get("ad_text") and not service and not _same(ad.get("ad_text"), title):  # 6 / 40 captions were product names without that gate
        out.append((_phrase((ad.get("ad_text") or "")[:300], noun=True), "ad_title"))
    if ad.get("llm_name") and grounded(ad["llm_name"], ad):
        out.append((_clean(ad["llm_name"]), "llm"))
    seen, res = set(), []
    for n, src in out:
        if not n or is_bad_name(n, adv):
            continue
        k = re.sub(r"\W+", "", n.lower())
        if k not in seen:
            seen.add(k)
            res.append((n[:120], src))
    return res


def grounded(name: str, ad: dict) -> bool:
    """A model-suggested name must use the ad's own words (any significant word appears in title / copy / url / hashtags).
    Non-Latin copy (Arabic, Thai…) is translated by the model, so there it counts as grounded."""
    blob = " ".join(str(ad.get(k) or "") for k in ("title", "ad_text", "landing_url", "product_url")).lower()
    if not blob.strip():
        return False
    if not re.search(r"[a-zà-ỹ]{3}", blob):
        return True
    squash = re.sub(r"[\W_]+", "", blob)
    return any(len(w) >= 4 and w in squash for w in re.findall(r"\w+", name.lower()))


def best(ad: dict) -> dict:
    """→ {name, source, status, confidence, evidence} — name None + status UNKNOWN when nothing qualifies."""
    cands = candidates(ad)
    if not cands:
        return {"name": None, "source": None, "status": "UNKNOWN", "confidence": 0.0, "evidence": []}
    name, src = cands[0]
    conf = SOURCE_PRECISION[src]
    # two independent sources naming the same thing (≥ half the words shared) confirm a weak one
    if conf < AUTO_CONFIRM_AT and any(s != src and overlap(name, n) >= 0.5 for n, s in cands[1:]):
        conf = AUTO_CONFIRM_AT
    return {"name": name, "source": src, "status": "AUTO_CONFIRMED" if conf >= AUTO_CONFIRM_AT else "SUGGESTED",
            "confidence": conf, "evidence": [{"source": s, "value": n} for n, s in cands[:4]]}


def result(name: str | None, source: str | None) -> dict:
    """A single (name, source) in best()'s shape."""
    if not name:
        return {"name": None, "source": None, "status": "UNKNOWN", "confidence": 0.0, "evidence": []}
    conf = SOURCE_PRECISION.get(source, 0.65)
    return {"name": name, "source": source, "status": "AUTO_CONFIRMED" if conf >= AUTO_CONFIRM_AT else "SUGGESTED",
            "confidence": conf, "evidence": [{"source": source, "value": name}]}


def _rank(p) -> int:
    return STATUS_RANK.get(p.name_status or "SUGGESTED", 1)  # NULL = named before this module (legacy) ≈ SUGGESTED


def _push_history(p, by: str | None) -> None:
    from datetime import datetime

    h = list(p.name_history or [])
    h.append({"name": p.canonical_name, "name_vi": p.name_vi, "status": p.name_status, "source": p.name_source,
              "confidence": p.name_confidence, "manual": bool(p.manual_override), "by": by, "at": datetime.utcnow().isoformat()})
    p.name_history = h[-20:]


def _add_evidence(p, evidence: list[dict]) -> None:
    have = list(p.name_evidence or [])
    keys = {(e.get("source"), (e.get("value") or "").lower()) for e in have}
    for e in evidence:
        if (e.get("source"), (e.get("value") or "").lower()) not in keys and len(have) < 6:
            have.append(e)
    p.name_evidence = have


def _rename(p, name: str) -> None:
    from .entity_resolution import index_name

    if p.canonical_name and p.canonical_name != UNKNOWN_LABEL and p.canonical_name.lower() != name.lower():
        from ..models import ProductAlias

        if p.canonical_name.lower() not in {a.name.lower() for a in p.aliases}:
            p.aliases.append(ProductAlias(name=p.canonical_name, source="previous_name", match_score=1.0))
    p.canonical_name = name
    p.name_vi = None  # re-translated by the naming job
    index_name(p.id, name)


def absorb(p, nm: dict, evidence: list[dict]) -> None:
    """An ad matched existing product `p`: keep its evidence; a strictly better source renames it — never a MKT name."""
    _add_evidence(p, evidence)
    if p.manual_override or not nm.get("name") or STATUS_RANK[nm["status"]] <= _rank(p):
        return
    _push_history(p, "auto")
    _rename(p, nm["name"])
    p.name_status, p.name_source, p.name_confidence = nm["status"], nm["source"], nm["confidence"]


def set_name(p, name: str | None, name_vi: str | None, by: str | None) -> None:
    """MKT edit: VERIFIED, locked against crawler renames, previous state kept for undo."""
    _push_history(p, by)
    if name and name.strip():
        _rename(p, name.strip()[:255])
    if name_vi is not None:
        p.name_vi = name_vi.strip()[:255] or None
    p.name_status, p.name_source, p.name_confidence, p.manual_override = "VERIFIED", "manual", 1.0, True


def confirm(p, by: str | None) -> None:
    """MKT accepts the suggested name as is (provenance kept in name_source)."""
    _push_history(p, by)
    p.name_status, p.name_confidence, p.manual_override = "VERIFIED", 1.0, True


def undo(p) -> bool:
    h = list(p.name_history or [])
    if not h:
        return False
    last = h.pop()
    if last["name"] and last["name"] != p.canonical_name:
        _rename(p, last["name"])
    p.name_vi, p.name_status, p.name_source = last["name_vi"], last["status"], last["source"]
    p.name_confidence, p.manual_override, p.name_history = last["confidence"], last["manual"] or None, h
    return True


def overlap(a: str, b: str) -> float:
    ta, tb = (set(w for w in re.findall(r"\w+", x.lower()) if len(w) > 2) for x in (a, b))
    return len(ta & tb) / min(len(ta), len(tb)) if ta and tb else 0.0


# ------------------------------------------------------------ what kind of ad is this
BRAND_ADVERTISER = re.compile(r"^(tik ?tok|shopee|lazada|temu|shein|alibaba|aliexpress|amazon|noon|tokopedia|zalora|lulu|carrefour|karfour|ikea)", re.I)
SERVICE_WORDS = re.compile(
    r"\b(insurance|loans?|credit card|online course|course|webinar|coaching|coach|real estate|condo|apartments?|villa|hotel|flights?|"
    r"clinic|dental|dentist|veneers?|salon|spa|therapy|therapist|tarot|reading|consultation|tutor|enroll|enrol|visa|recruitment|hiring|job|"
    r"nghỉ dưỡng|liệu trình|phòng khám|thẩm mỹ viện|"
    r"bảo hiểm|khóa học|khoá học|tuyển dụng|căn hộ|nha khoa|tư vấn|تأمين|دورة|شقة|عيادة)\b", re.I)


PLACEHOLDER = re.compile(r"\(chưa đặt tên\)|^unknown| creative$|^chưa xác định sản phẩm$", re.I)


def is_bad_product_name(name: str | None, advertiser: str | None = None) -> bool:
    """A stored product name that is a placeholder, a shop, a CTA or a headline sentence (re-derived by the backfill)."""
    n = _clean(name)
    return bool(PLACEHOLDER.search(name or "") or is_bad_name(name, advertiser) or PRONOUN.search(n) or SENTENCE.search(n)
                or FIRST_WORD.match(n) or AR_YOU.search(n))


def ad_type(ad: dict, naming: dict | None = None) -> str:
    """PRODUCT_AD · MULTI_PRODUCT_AD · SERVICE_AD · CONTENT_AD · BRAND_AD · UNKNOWN (rules only, no model)."""
    from . import content_filter

    copy = " ".join(str(ad.get(k) or "") for k in ("title", "ad_text"))
    url = ad.get("landing_url") or ad.get("product_url") or ""
    verdict, _ = content_filter.classify(ad.get("advertiser"), ad.get("title"), ad.get("ad_text"), ad.get("cta_type"),
                                         ad.get("cta_text"), url)
    if verdict == "block":
        return "CONTENT_AD"
    host = urlparse(url).netloc.lower()
    shop_deeplink = re.search(r"ec(%2F|/)(mall|product|pdp)", url, re.I)  # TikTok Shop's onelink → in-app shop, still a product ad
    if not shop_deeplink and (ad.get("funnel") in ("app", "form") or re.search(r"play\.google|apps\.apple|itunes\.apple|onelink\.me", host)) \
            or SERVICE_WORDS.search(copy) or ad.get("category") == "non_product" and SERVICE_WORDS.search(copy + " " + (ad.get("advertiser") or "")):
        return "SERVICE_AD"
    naming = naming or best(ad)
    if naming["name"]:
        return "PRODUCT_AD"
    if re.search(r"/collections?(/|$)|/(shop|store|category|categories)(/|$)", urlparse(url).path) or \
            re.search(r"(?<!\w)(collection|bộ sưu tập|items|products|mga produkto)(?!\w)", copy, re.I):
        return "MULTI_PRODUCT_AD"
    if BRAND_ADVERTISER.search(ad.get("advertiser") or ""):
        return "BRAND_AD"
    return "UNKNOWN"


def display_name(p) -> str:
    """What a card shows: the evidenced name in its own language, else the honest 'not identified'.
    A Vietnamese name is shown only when MKT wrote it (manual_override); machine translations stay search-only keywords."""
    if getattr(p, "name_status", None) == "UNKNOWN":
        return UNKNOWN_LABEL
    return shown_vi(p) or p.canonical_name


def shown_vi(p) -> str | None:
    return getattr(p, "name_vi", None) if getattr(p, "manual_override", False) else None


def card_fields(p) -> dict:
    """Naming block every product card carries (search grid, tables, Product 360)."""
    hint = next((e.get("value") for e in p.name_evidence or [] if e.get("source") in ("legacy_hint", "llm_hint", "rule_hint")), None)
    provisional = p.name_status == "UNKNOWN" and bool(getattr(p, "shop_name", None))
    return {"name": f"{'Quảng cáo' if p.shop_service else 'Sản phẩm'} từ {p.shop_name}" if provisional else display_name(p), "name_provisional": provisional,
            "shop_name": p.shop_name if provisional else None,
            "canonical_name": None if p.name_status == "UNKNOWN" else p.canonical_name,
            "name_vi": shown_vi(p), "name_status": p.name_status or "SUGGESTED", "name_source": p.name_source or "legacy",
            "name_confidence": p.name_confidence, "name_hint": hint, "manual_override": bool(p.manual_override),
            "name_evidence": [e for e in p.name_evidence or [] if e.get("source") not in ("legacy_hint", "llm_hint", "rule_hint")]}


if __name__ == "__main__":
    ok = lambda n, **ad: best(ad)["name"] == n or print("FAIL", n, best(ad))
    ok(None, title="Click Order Now")
    ok(None, title="TikTok Shop", advertiser="TikTok Shop")
    ok(None, title="Blue Light Shopping", advertiser="Blue Light Shopping")
    ok("Lucky Fortune Transfer Dragon Bracelets Jewelry Fish Bangles", channel="commerce",
       title="New Creative Fashion Personality Lucky Fortune Transfer Dragon Bracelets Jewelry Fish Bangles")
    ok("Affordable Universal Stylus Pen", title="Discover Affordable Universal Stylus Pen for your Device! #styluspen")
    ok("Neck Massager Pro", landing_url="https://shop.example.com/products/neck-massager-pro")
    print("ok")
