"""Language-aware text evidence for the Wave / Creative signals (docs: Wave multilingual, 2026-10-14).

The signals used to be regexes over `title + ad_text` only. For a Tagalog / Arabic ad, or any ad with a two-word caption, a
regex miss meant "this ad has no curiosity / demo / UGC" — a guess, and a wrong one. Here the text an ad *really* has is
collected first (caption, headline, and the OCR / transcript columns when a creative already carries them), its language is
read, and a **reliability** (0-1) says how far the regexes can be trusted on it. discovery.py shrinks the text-derived parts
towards a neutral value by that reliability and exposes it as `wave_confidence`: missing data lowers confidence, it does
not become a low score.

Lexicons cover en · vi · tl (Tagalog) · ar. Other languages are detected (so the UI can say so) but no lexicon reads them,
so their reliability is halved.
"""
from __future__ import annotations

import re
from collections import Counter

_A, _Z = "(?<![؀-ۿ])", "(?![؀-ۿ])"  # Arabic has no : short roots ("سر" secret vs "سرير" bed) need explicit edges
# ------------------------------------------------------------ lexicons (one regex per signal, all four languages)
CURIOSITY = re.compile(
    r"\?|bí mật|bạn có biết|cái gì|không ai|mẹo|"                               # vi
    r"secret|did you know|you won't believe|wait for it|what is this|tiktok made me|viral|nobody|hack|trick|"  # en
    r"sikreto|alam mo ba|hindi mo aakalain|nakakagulat|bakit|paano|walang nagsasabi|"  # tl
    r"لن تصدق|" + _A + "سر" + _Z + r"|هل تعلم|ماذا|لماذا|كيف|مفاجأة|لا أحد", re.I)                     # ar
DEMO = re.compile(
    r"\bxem\b|cách (dùng|làm)|chỉ cần|thử|"                                         # vi
    r"watch|demo|how (it|to)|in \d+ (sec|giây)|just|step|test|"                      # en
    r"panoorin|tingnan|paano (gamitin|ito)|sa loob ng \d+|subukan|ipakita|madaling gamitin|"  # tl
    r"شاهد|طريقة (الاستخدام|استخدام)|جرب|خطوات|في \d+ (ثانية|ثواني|دقيقة)|بسهولة", re.I)  # ar
TRANSFORM = re.compile(
    r"trước|sau \d+|kết quả|thay đổi|"                                              # vi
    r"before|after|results?|transformation|"                                         # en
    r"bago at pagkatapos|pagkatapos ng \d+|resulta|pagbabago|"                       # tl
    r"قبل|بعد|نتيجة|النتائج|تحول", re.I)                                             # ar
UGC = re.compile(
    r"\b(tôi|mình|em|chị|anh)\b|review|unbox|trải nghiệm|dùng thử|"                  # vi
    r"\b(i|my|me)\b|testimonial|tried|"                                              # en
    r"\b(ako|ko|akin|aking)\b|sinubukan ko|testimonya|"                              # tl
    r"جربت|تجربتي|" + _A + "أنا" + _Z + r"|رأيي|استخدمت|اشتريت", re.I)                                     # ar
EMOTION = re.compile(
    r"\b(mẹ|quà|yêu|gia đình|bé|con)\b|"                                            # vi
    r"mom|mother|gift|love|family|baby|eid|ramadan|valentine|"                       # en
    r"nanay|regalo|mahal|pamilya|anak|pasko|ina\b|"                                  # tl
    r"هدية|" + _A + "(أم|حب|عيد)" + _Z + r"|عائلة|أطفال|رمضان", re.I)                                       # ar

# ------------------------------------------------------------ language
_TL = frozenset("ang mga ng sa na ay para ito ko mo namin kayo po ba lang din rin hindi yung pang kasi pero dahil gusto may mag "
                "naman talaga sobra ito ka siya natin nila akin kung lahat mas ang".split())
_EN = frozenset("the and for with you your this that is are to of in on it my not from have has be can will just get all more "
                "free now new best".split())
_VI_DIACRITIC = re.compile(r"[ăâđêôơưạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹ]", re.I)
_SCRIPTS = (("ar", re.compile(r"[؀-ۿݐ-ݿ]")), ("th", re.compile(r"[฀-๿]")),
            ("zh", re.compile(r"[一-鿿]")), ("ja/ko", re.compile(r"[぀-ヿ가-힯]")), ("ru", re.compile(r"[Ѐ-ӿ]")))
LEXICON_LANGS = {"en", "vi", "tl", "ar"}
_WORD = re.compile(r"[^\W\d_]+", re.U)
_NOISE = re.compile(r"https?://\S+|#\w+|@\w+")  # urls / hashtags / handles are not copy


def clean(text: str | None) -> str:
    return _NOISE.sub(" ", text or "").strip()


def word_count(text: str | None) -> int:
    return len(_WORD.findall(clean(text)))


def detect_lang(text: str | None) -> tuple[str, float]:
    """(code, confidence 0-1). 'und' when there is too little to say. Script first (ar / th / zh…), then Latin-script stop-words:
    Tagalog vs English vs Vietnamese (diacritics). A Taglish ad scores as whichever side has more function words."""
    t = clean(text)
    if not t:
        return "und", 0.0
    letters = max(1, sum(ch.isalpha() for ch in t))
    for code, rx in _SCRIPTS:
        share = len(rx.findall(t)) / letters
        if share >= 0.3:
            return code, min(1.0, 0.5 + share / 2)
    words = [w.lower() for w in _WORD.findall(t)]
    if len(words) < 3:
        return "und", 0.0
    vi = len(_VI_DIACRITIC.findall(t)) / letters
    if vi >= 0.12:
        return "vi", min(1.0, 0.5 + vi * 2)
    tl, en = sum(w in _TL for w in words), sum(w in _EN for w in words)
    if tl == en == 0:
        return "und", 0.0
    lang, hits = ("tl", tl) if tl > en else ("en", en)
    return lang, min(1.0, 0.4 + 2.5 * hits / len(words))


# ------------------------------------------------------------ per-ad evidence
ENOUGH_WORDS = 12  # a full sentence or two: reliability 1.0
MIN_WORDS = 3      # below this the copy is a label ("Shop now"): no signal can be read


def ad_text_evidence(ad, ocr: str | None = None, transcript: str | None = None) -> dict:
    """What an ad's text really offers. `sources` lists where it came from (caption · title · ocr · transcript), `reliability`
    how far the regex signals can be trusted on it (0 = unknown, never "negative")."""
    parts = {"caption": clean(ad.ad_text), "title": clean(ad.title), "ocr": clean(ocr), "transcript": clean(transcript)}
    if parts["title"] and parts["title"] in parts["caption"]:
        parts["title"] = ""
    parts = {k: v for k, v in parts.items() if word_count(v) >= 1}
    text = " ".join(parts.values())
    words = word_count(text)
    lang, conf = detect_lang(text)
    if words < MIN_WORDS:
        rel = 0.0
    else:
        rel = min(1.0, words / ENOUGH_WORDS)
        rel *= 1.0 if lang in LEXICON_LANGS else 0.5 if lang != "und" else 0.6  # unread language / no language evidence
    return {"text": text, "sources": list(parts), "words": words, "lang": lang, "lang_conf": round(conf, 2),
            "reliability": round(rel, 3)}


def needs_text(ev: dict, bar: float = 0.5) -> bool:
    """Worth spending OCR / transcript on: the copy is too thin for the regexes to be believed."""
    # an unread language (es, ja…) stays unreadable whatever we OCR: no lexicon covers it, so don't spend on it — its low
    # reliability already lowers the confidence
    return ev["reliability"] < bar and ev["lang"] in LEXICON_LANGS | {"und"}


def summarize(evs: list[dict]) -> dict:
    """Product level: coverage = mean reliability over its ads (0-1); langs / sources for the UI."""
    n = len(evs)
    langs = Counter(e["lang"] for e in evs if e["lang"] != "und")
    srcs = Counter(s for e in evs for s in e["sources"])
    return {"coverage": round(sum(e["reliability"] for e in evs) / n, 3) if n else 0.0,
            "ads": n, "readable_ads": sum(1 for e in evs if e["reliability"] > 0),
            "langs": dict(langs.most_common()), "sources": dict(srcs),
            "unread_langs": sorted(l for l in langs if l not in LEXICON_LANGS)}
