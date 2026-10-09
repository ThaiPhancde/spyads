"""AI Enrichment (blueprint §2, §15-17, §20): unstructured → structured.

Rule-based classifiers ship by default (vi + en lexicons). When an Anthropic key
is configured, comment ABSA is upgraded to Claude via structured outputs, in batches.
"""
import hashlib
import re

from . import llm

# ============================================================ helpers
def _has(text: str, words) -> bool:
    for w in words:
        if re.search(rf"(?<!\w){re.escape(w)}(?!\w)", text):
            return True
    return False


# ============================================================ Comment ABSA (§17)
ASPECTS = [
    "quality", "effectiveness", "price", "shipping", "packaging", "authenticity", "trust",
    "sizing", "usability", "side_effects", "support", "refund",
]

# aspect → (trigger words, positive words, negative words)
ASPECT_LEXICON: dict[str, tuple[list[str], list[str], list[str]]] = {
    "quality": (
        ["chất lượng", "sản phẩm", "hàng", "quality", "product", "material", "chất liệu", "dùng", "xài", "item", "build"],
        ["tốt", "ổn", "đẹp", "xịn", "bền", "chắc chắn", "good", "great", "excellent", "solid", "love", "amazing", "nice", "perfect", "tuyệt", "ưng"],
        ["kém", "tệ", "dở", "hỏng", "hư", "rởm", "cheap quality", "broke", "broken", "bad", "poor", "terrible", "flimsy", "lỗi", "defective", "doesn't match", "không giống", "khác hình"],
    ),
    "effectiveness": (
        ["hiệu quả", "tác dụng", "works", "work", "effective", "result", "kết quả", "giảm", "hết", "đỡ"],
        ["hiệu quả", "có tác dụng", "works", "worked", "effective", "đỡ đau", "hết đau", "hết mụn", "giảm", "results", "thấy rõ", "helps", "helped"],
        ["không hiệu quả", "không tác dụng", "chẳng tác dụng", "doesn't work", "didn't work", "useless", "no effect", "vô dụng", "không thấy"],
    ),
    "price": (
        ["giá", "tiền", "price", "cost", "expensive", "cheap", "đắt", "rẻ", "worth", "sar", "aed", "đ"],
        ["rẻ", "hợp lý", "đáng tiền", "worth", "affordable", "good price", "giá tốt", "cheap"],
        ["đắt", "mắc", "cao", "hơi cao", "expensive", "overpriced", "too much", "not worth", "chát"],
    ),
    "shipping": (
        ["giao", "ship", "shipping", "delivery", "vận chuyển", "nhận hàng", "courier", "giao hàng", "arrived", "deliver"],
        ["nhanh", "fast", "quick", "đúng hẹn", "on time", "sớm"],
        ["chậm", "lâu", "slow", "late", "delay", "delayed", "never arrived", "chưa nhận", "không nhận được", "mất hàng"],
    ),
    "packaging": (
        ["đóng gói", "hộp", "package", "packaging", "box", "bao bì"],
        ["cẩn thận", "đẹp", "chắc", "well packed", "nice box"],
        ["móp", "vỡ", "rách", "bể", "damaged", "broken box", "crushed", "sơ sài"],
    ),
    "authenticity": (
        ["chính hãng", "hàng thật", "fake", "authentic", "original", "real", "giả", "nhái"],
        ["chính hãng", "authentic", "hàng thật", "genuine", "real"],
        ["fake", "giả", "nhái", "counterfeit", "replica"],
    ),
    "trust": (
        ["lừa", "scam", "uy tín", "trust", "legit", "lừa đảo", "shop", "page", "seller"],
        ["uy tín", "legit", "trustworthy", "tin tưởng", "recommend"],
        ["lừa", "lừa đảo", "scam", "scammer", "fraud", "không uy tín", "cẩn thận", "đừng mua", "don't buy"],
    ),
    "sizing": (
        ["size", "kích thước", "rộng", "chật", "fit", "small", "big", "nhỏ", "to"],
        ["vừa", "fits", "fit well", "vừa vặn", "true to size"],
        ["rộng", "chật", "too small", "too big", "nhỏ quá", "to quá", "doesn't fit"],
    ),
    "usability": (
        ["dùng", "sử dụng", "use", "easy", "khó", "dễ", "setup", "hướng dẫn", "manual", "pin", "battery", "sạc"],
        ["dễ dùng", "dễ sử dụng", "easy to use", "easy", "tiện", "convenient", "simple", "pin trâu"],
        ["khó dùng", "khó sử dụng", "hard to use", "complicated", "confusing", "không biết dùng", "pin yếu", "hết pin nhanh"],
    ),
    "side_effects": (
        ["kích ứng", "dị ứng", "rát", "ngứa", "đỏ", "irritation", "rash", "allergic", "burn", "side effect", "nổi mụn"],
        [],
        ["kích ứng", "dị ứng", "rát", "ngứa", "irritation", "rash", "allergic", "burn", "burning", "nổi mụn", "side effect", "side effects"],
    ),
    "support": (
        ["cskh", "tư vấn", "support", "customer service", "inbox", "rep", "trả lời", "hỗ trợ", "seller"],
        ["nhiệt tình", "helpful", "responsive", "tư vấn kỹ", "hỗ trợ tốt", "fast reply"],
        ["không trả lời", "no reply", "ignored", "không ai trả lời", "rude", "thái độ"],
    ),
    "refund": (
        ["hoàn tiền", "refund", "trả hàng", "return", "đổi trả", "money back"],
        ["hoàn tiền nhanh", "refunded", "đã hoàn", "easy return"],
        ["không hoàn", "no refund", "không cho đổi", "refuse refund", "chưa hoàn", "want refund", "muốn hoàn"],
    ),
}

GENERIC_POS = ["tốt", "tuyệt", "thích", "ưng", "love", "great", "amazing", "good", "perfect", "recommend", "best", "👍", "❤️", "😍", "đỉnh", "xịn"]
GENERIC_NEG = ["tệ", "dở", "thất vọng", "bad", "terrible", "worst", "disappointed", "awful", "scam", "lừa", "😡", "👎", "phí tiền", "waste"]
INTENT_WORDS = ["muốn mua", "đặt", "order", "how to buy", "mua ở đâu", "còn hàng", "ib", "inbox", "giá bao nhiêu", "how much",
                "where to buy", "link", "ship về", "lấy 1", "lấy 2", "cho mình 1", "i want", "need this", "want one", "pm"]
QUESTION_WORDS = ["?", "bao nhiêu", "how much", "how", "where", "có ship", "có không", "không ạ", "được không", "là gì", "when"]

CLAUSE_SPLIT = re.compile(r"(?:,|\.|;|!|\bnhưng\b|\bbut\b|\bmà\b|\bvà\b|\band\b|\btuy nhiên\b|\bhowever\b)", re.IGNORECASE)


def analyze_comment_rules(text: str) -> dict:
    t = (text or "").lower()
    aspects: dict[str, str] = {}
    for clause in CLAUSE_SPLIT.split(t):
        clause = clause.strip()
        if not clause:
            continue
        specific = False
        for aspect, (triggers, pos, neg) in ASPECT_LEXICON.items():
            if aspect == "quality" or not _has(clause, triggers):
                continue
            if _has(clause, neg):
                aspects[aspect] = "negative"
                specific = True
            elif pos and _has(clause, pos):
                aspects.setdefault(aspect, "positive")
                specific = True
            # trigger without an opinion word → mention only, not tagged
        if specific:
            continue
        # No specific aspect in this clause → opinion words are about the product itself
        _, qpos, qneg = ASPECT_LEXICON["quality"]
        if _has(clause, qneg) or _has(clause, GENERIC_NEG):
            aspects["quality"] = "negative"
        elif _has(clause, qpos) or _has(clause, GENERIC_POS):
            aspects.setdefault("quality", "positive")

    pos = sum(1 for v in aspects.values() if v == "positive") + (1 if not aspects and _has(t, GENERIC_POS) else 0)
    neg = sum(1 for v in aspects.values() if v == "negative") + (1 if not aspects and _has(t, GENERIC_NEG) else 0)
    if pos and neg and abs(pos - neg) <= 1:
        overall = "neutral"
    elif pos > neg:
        overall = "positive"
    elif neg > pos:
        overall = "negative"
    else:
        overall = "neutral"

    is_question = _has(t, QUESTION_WORDS) or t.strip().endswith("?")
    if _has(t, INTENT_WORDS):
        intent = "high"
    elif overall == "negative" or aspects.get("trust") == "negative":
        intent = "low"
    elif pos > 0 or is_question:
        intent = "medium"
    else:
        intent = "low"
    return {"overall": overall, "aspects": aspects, "purchase_intent": intent, "is_question": is_question}


_ABSA_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "i": {"type": "integer"},
                    "overall": {"type": "string", "enum": ["positive", "neutral", "negative"]},
                    "aspects": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "aspect": {"type": "string", "enum": ASPECTS},
                                "polarity": {"type": "string", "enum": ["positive", "negative"]},
                            },
                            "required": ["aspect", "polarity"],
                            "additionalProperties": False,
                        },
                    },
                    "purchase_intent": {"type": "string", "enum": ["high", "medium", "low"]},
                    "is_question": {"type": "boolean"},
                },
                "required": ["i", "overall", "aspects", "purchase_intent", "is_question"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["results"],
    "additionalProperties": False,
}


def analyze_comments(texts: list[str]) -> tuple[list[dict], str]:
    """Batch ABSA. Returns (results, engine)."""
    if llm.available() and texts:
        out: list[dict] = []
        for start in range(0, len(texts), 40):
            chunk = texts[start:start + 40]
            numbered = "\n".join(f"{i}. {c}" for i, c in enumerate(chunk))
            data = llm.complete_json(
                system=(
                    "You do aspect-based sentiment analysis on e-commerce ad comments and reviews "
                    "(Vietnamese, English, Arabic, Thai...). Only tag an aspect when the commenter expresses an "
                    "opinion about it. Return one result per numbered comment, using its number as `i`."
                ),
                user=numbered,
                schema=_ABSA_SCHEMA,
            )
            if not data:
                out = []
                break
            by_i = {r["i"]: r for r in data["results"]}
            for i, c in enumerate(chunk):
                r = by_i.get(i)
                if r is None:
                    out.append(analyze_comment_rules(c))
                    continue
                out.append({
                    "overall": r["overall"],
                    "aspects": {a["aspect"]: a["polarity"] for a in r["aspects"]},
                    "purchase_intent": r["purchase_intent"],
                    "is_question": r["is_question"],
                })
        if out:
            return out, "claude"
    return [analyze_comment_rules(t) for t in texts], "rules"


# ============================================================ Creative: hook / angle / offer (§20)
# vi + en + a few ar (GCC) / tl (PH) words — the two markets the team actually spies
HOOKS = {
    "question": ["?", "bạn có", "do you", "are you", "have you", "có phải", "هل", "ba"],
    "problem": ["đau", "mệt", "khổ", "tired of", "struggling", "pain", "problem", "suffer", "mụn", "rụng tóc", "ألم", "تعب", "sakit", "pagod", "hirap"],
    "before_after": ["before", "after", "trước và sau", "trước sau", "transformation", "sau 7 ngày", "after 7 days", "قبل وبعد"],
    "testimonial": ["review", "khách hàng", "customer", "feedback", "chị", "em đã dùng", "i tried", "my mom", "testimonial", "تجربتي", "sinubukan ko"],
    "demo": ["xem", "watch", "how it works", "cách dùng", "chỉ cần", "just", "demo", "in seconds", "شاهد", "panoorin", "tingnan"],
    "urgency": ["hôm nay", "today", "only", "chỉ còn", "limited", "last chance", "hurry", "flash sale", "cuối cùng", "اليوم", "عرض محدود", "ngayon", "limitado", "huling"],
    "curiosity": ["bí mật", "secret", "nobody tells", "không ai nói", "you won't believe", "viral", "tiktok made me", "سر", "لن تصدق", "sikreto"],
}
ANGLES = {
    "pain_relief": ["đau", "pain", "relief", "nhức", "mỏi", "đỡ", "relax", "thư giãn", "ألم", "راحة", "sakit", "ginhawa"],
    "beauty": ["đẹp", "da", "skin", "glow", "trắng", "beauty", "mụn", "acne", "nếp nhăn", "wrinkle", "tóc", "hair", "جمال", "بشرة", "kutis", "maganda", "pampaputi"],
    "convenience": ["tiện", "easy", "nhanh", "save time", "tiết kiệm thời gian", "portable", "mang theo", "anywhere", "سهل", "سريع", "madali", "mabilis", "praktikal"],
    "health": ["sức khỏe", "health", "healthy", "posture", "ngủ", "sleep", "tư thế", "صحة", "kalusugan", "tulog"],
    "saving_money": ["tiết kiệm", "save money", "rẻ hơn", "cheaper", "thay vì", "instead of", "توفير", "أرخص", "tipid", "mura", "sulit"],
    "gift": ["quà", "gift", "tặng", "mother", "mẹ", "valentine", "eid", "ramadan", "هدية", "regalo", "pasko", "nanay"],
    "pet_care": ["pet", "chó", "mèo", "dog", "cat", "قطة", "كلب", "aso", "pusa", "alaga"],
}
OFFER_PATTERNS = [
    (r"(\d{1,2})\s?%\s?(off|giảm|discount)|giảm\s?(\d{1,2})\s?%", "discount"),
    (r"free\s?ship|freeship|miễn phí vận chuyển|miễn phí ship|free delivery|libreng (?:shipping|delivery|padala)|توصيل مجاني", "free_shipping"),
    (r"buy\s?1\s?get\s?1|mua 1 tặng 1|bogo|1\+1", "bogo"),
    (r"\bcod\b|thanh toán khi nhận|cash on delivery|bayad pagdating|الدفع عند الاستلام", "cod"),
    (r"bảo hành|warranty|guarantee|hoàn tiền|money back", "guarantee"),
    (r"tặng kèm|free gift|quà tặng", "free_gift"),
]


def classify_creative(text: str | None) -> dict:
    t = (text or "").lower()
    hook = next((h for h, w in HOOKS.items() if _has(t, w) or (h == "question" and "?" in t[:80])), "statement")
    angle = next((a for a, w in ANGLES.items() if _has(t, w)), "general")
    offers = [name for pat, name in OFFER_PATTERNS if re.search(pat, t)]
    return {"hook": hook, "angle": angle, "offer": ",".join(dict.fromkeys(offers)) or None}


def creative_fingerprint(text: str | None, media_url: str | None) -> str:
    """Near-duplicate key: normalized first 60 chars of copy + media path."""
    norm = re.sub(r"\W+", "", (text or "").lower())[:60]
    media = (media_url or "").split("?")[0].rsplit("/", 1)[-1]
    return hashlib.sha1(f"{norm}|{media}".encode()).hexdigest()[:16]


# ============================================================ Market classifier (§7)
COUNTRY_INFO = {
    "SA": ("Saudi Arabia", "GCC", "ar", "SAR"), "AE": ("UAE", "GCC", "ar", "AED"), "KW": ("Kuwait", "GCC", "ar", "KWD"),
    "QA": ("Qatar", "GCC", "ar", "QAR"), "OM": ("Oman", "GCC", "ar", "OMR"), "BH": ("Bahrain", "GCC", "ar", "BHD"),
    "VN": ("Vietnam", "SEA", "vi", "VND"), "TH": ("Thailand", "SEA", "th", "THB"), "PH": ("Philippines", "SEA", "en", "PHP"),
    "MY": ("Malaysia", "SEA", "ms", "MYR"), "ID": ("Indonesia", "SEA", "id", "IDR"),
    "JO": ("Jordan", "ME", "ar", "JOD"), "EG": ("Egypt", "ME", "ar", "EGP"), "IQ": ("Iraq", "ME", "ar", "IQD"),
    "US": ("United States", "NA", "en", "USD"), "CA": ("Canada", "NA", "en", "CAD"), "GB": ("United Kingdom", "EU", "en", "GBP"),
    "DE": ("Germany", "EU", "de", "EUR"), "FR": ("France", "EU", "fr", "EUR"), "IT": ("Italy", "EU", "it", "EUR"),
    "ES": ("Spain", "EU", "es", "EUR"), "NL": ("Netherlands", "EU", "nl", "EUR"), "BE": ("Belgium", "EU", "nl", "EUR"),
    "AT": ("Austria", "EU", "de", "EUR"), "IE": ("Ireland", "EU", "en", "EUR"), "PT": ("Portugal", "EU", "pt", "EUR"),
    "FI": ("Finland", "EU", "fi", "EUR"), "GR": ("Greece", "EU", "el", "EUR"), "SE": ("Sweden", "EU", "sv", "SEK"),
    "DK": ("Denmark", "EU", "da", "DKK"), "CZ": ("Czechia", "EU", "cs", "CZK"),
    "PL": ("Poland", "EU", "pl", "PLN"), "RO": ("Romania", "EU", "ro", "RON"),
    "AU": ("Australia", "AU", "en", "AUD"), "NZ": ("New Zealand", "AU", "en", "NZD"),
}
# where COD is the default checkout (markets.py targets): GCC + Levant/Egypt/Iraq, SEA, southern/eastern EU, AU/NZ
# ponytail: DE/FR/NL/SE/DK/FI/IE/AT/BE are prepaid markets — not COD, even though they are targets
COD_COUNTRIES = {"SA", "AE", "KW", "QA", "OM", "BH", "JO", "EG", "IQ", "VN", "TH", "PH", "MY", "ID",
                 "PL", "RO", "IT", "ES", "GR", "CZ", "PT", "AU", "NZ"}
# rough USD conversion for price segmentation
FX_TO_USD = {"SAR": 0.27, "AED": 0.27, "KWD": 3.25, "QAR": 0.27, "OMR": 2.6, "BHD": 2.65, "JOD": 1.41, "EGP": 0.021,
             "IQD": 0.00076, "VND": 0.00004, "THB": 0.028, "PHP": 0.018, "MYR": 0.21, "IDR": 0.000063,
             "USD": 1, "CAD": 0.73, "GBP": 1.27, "EUR": 1.08, "AUD": 0.65, "NZD": 0.60, "SEK": 0.095, "DKK": 0.145,
             "CZK": 0.043, "PLN": 0.25, "RON": 0.22, "CNY": 0.14, "RMB": 0.14}


def price_segment(price: float | None, currency: str | None) -> str | None:
    if not price:
        return None
    usd = price * FX_TO_USD.get((currency or "USD").upper(), 1)
    if usd < 15:
        return "budget (<$15)"
    if usd < 35:
        return "mid ($15-35)"
    if usd < 70:
        return "upper ($35-70)"
    return "premium (>$70)"


def to_usd(price: float | None, currency: str | None) -> float | None:
    if price is None:
        return None
    return price * FX_TO_USD.get((currency or "USD").upper(), 1)


def classify_market(country: str | None, platform: str | None, category: str | None, price: float | None,
                    currency: str | None, ad_text: str | None = None) -> dict:
    code = (country or "").upper()
    name, region, lang, cur = COUNTRY_INFO.get(code, (country, None, None, currency))
    t = (ad_text or "").lower()
    funnel = "COD" if (code in COD_COUNTRIES or re.search(OFFER_PATTERNS[3][0], t)) else "Prepaid"
    if re.search(r"whatsapp|messenger|inbox|nhắn tin|ib", t):
        funnel = f"{funnel} + Chat"
    return {
        "country": code or None, "country_name": name, "region": region, "language": lang,
        "platform": platform, "category": category, "price_segment": price_segment(price, currency or cur),
        "currency": currency or cur, "funnel_type": funnel,
    }


# ============================================================ Category classifier
# en + vi, then ar (GCC / Egypt / Iraq) and tl (Philippines) — _has() matches whole words, Arabic letters count as \w
CATEGORY_KEYWORDS = {
    "beauty": ["serum", "cream", "kem", "acne", "mụn", "skin", "da", "whitening", "lipstick", "makeup", "hair", "tóc", "perfume", "nước hoa", "mask", "collagen",
               "سيروم", "كريم", "بشرة", "تفتيح", "تبييض", "شعر", "عطر", "مكياج", "ماسك", "حب الشباب", "كولاجين", "أحمر شفاه",
               "pampaputi", "kutis", "mukha", "buhok", "pabango", "tigyawat", "kolorete", "sabon", "lotion", "balat"],
    "health": ["massager", "massage", "posture", "corrector", "knee", "brace", "pain", "đau", "neck", "back", "foot", "sleep",
               "مساج", "مدلك", "ألم", "آلام", "ركبة", "ظهر", "رقبة", "قدم", "نوم", "مشد", "دعامة", "علاج",
               "masahe", "sakit", "likod", "tuhod", "leeg", "paa", "tulog", "balakang", "braso", "pamamaga"],
    "home": ["kitchen", "blender", "vacuum", "cleaner", "mop", "organizer", "lamp", "light", "pillow", "bếp", "nồi", "chảo", "cleaning",
             "مطبخ", "خلاط", "مكنسة", "تنظيف", "منظم", "مصباح", "إضاءة", "وسادة", "قدر", "مقلاة", "منزل",
             "kusina", "walis", "panlinis", "ilaw", "unan", "kaldero", "kawali", "lalagyan", "bahay", "linis"],
    "gadgets": ["phone", "charger", "earbuds", "camera", "smart", "projector", "drone", "watch", "led", "usb", "bluetooth",
                "جوال", "هاتف", "شاحن", "سماعة", "كاميرا", "ساعة ذكية", "بروجكتر", "بلوتوث", "ذكي", "درون",
                "cellphone", "relo", "kamera", "powerbank", "earphone", "speaker", "selpon", "pang-charge"],
    "fashion": ["dress", "shoe", "bag", "túi", "giày", "váy", "áo", "jeans", "abaya", "jewelry", "ring", "necklace",
                "فستان", "حذاء", "شنطة", "حقيبة", "عباية", "مجوهرات", "خاتم", "عقد", "قميص", "بنطلون", "جينز",
                "damit", "sapatos", "tsinelas", "pantalon", "alahas", "singsing", "kwintas", "blusa", "palda", "sando"],
    "pets": ["pet", "dog", "cat", "chó", "mèo", "leash", "litter",
             "قطط", "قطة", "كلب", "كلاب", "حيوانات أليفة", "طيور", "رمل قطط", "أليف",
             "aso", "pusa", "alaga", "tali", "pagkain ng aso", "pagkain ng pusa", "kulungan"],
}


# Not importable from China as a generic product (1688 / AliExpress): consumables and regulated goods, services,
# digital goods, and big brands that only sell their own stock. Matching products get category "non_product" and
# are left out of discovery views. The LLM pass (extract_products) decides first when an API key is set.
NOT_IMPORTABLE = re.compile(
    r"\b(supplements?|multivitamins?|vitamin (tablets|gummies|capsules)|protein powder|probiotics?|detox tea|slimming tea|weight loss (pills?|capsules?)|"
    r"medicine|medication|prescription|cbd|insurance|loans?|credit card|online course|webinar|real estate|apartments?|villa|"
    r"hotel|flights?|nike|adidas|rolex|"  # brand goods = counterfeit risk; "iPhone case" stays (generic accessory)
    r"thực phẩm chức năng|viên uống|thuốc|khoá học|khóa học|bảo hiểm|căn hộ|"
    r"مكمل|فيتامين|دواء|دورة|تأمين|شقة)\b", re.I)


def classify_category(name: str, text: str | None = None) -> str | None:
    t = f"{name} {text or ''}".lower()
    if NOT_IMPORTABLE.search(name or ""):  # the product name only: ad copy mentions "tea" / "app" too often
        return "non_product"
    best, hits = None, 0
    for cat, words in CATEGORY_KEYWORDS.items():
        n = sum(1 for w in words if _has(t, [w]))
        if n > hits:
            best, hits = cat, n
    return best


# ============================================================ Product name extraction (LLM, optional)
_PRODUCT_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "i": {"type": "integer"},
                    "product_name": {"type": ["string", "null"], "description": "short generic product name in English, e.g. 'Electric Neck Massager'"},
                    "is_physical_product": {"type": "boolean"},
                    "category": {"type": "string", "enum": ["beauty", "health", "home", "gadgets", "fashion", "pets", "kids", "food", "other", "service"]},
                },
                "required": ["i", "product_name", "is_physical_product", "category"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["results"],
    "additionalProperties": False,
}


def extract_products(items: list[dict]) -> list[dict] | None:
    """items: [{text, title, url}] → [{product_name, is_physical_product, category}] or None without LLM."""
    if not llm.available() or not items:
        return None
    out: list[dict] = []
    for start in range(0, len(items), 25):
        chunk = items[start:start + 25]
        lines = "\n".join(f"{i}. title={c.get('title')!r} url={c.get('url')!r} text={(c.get('text') or '')[:400]!r}" for i, c in enumerate(chunk))
        data = llm.complete_json(
            system=("You read e-commerce ads (any language) and name the product being sold as a short generic English "
                    "product name (no brand hype, no prices). is_physical_product=false for services, apps, media, "
                    "events, real estate, courses, and also for anything that cannot be imported from China as a "
                    "generic product: food, drinks, supplements, medicine, and branded goods sold only by the brand "
                    "(an iPhone is not importable, an iPhone case is). Use the number as i."),
            user=lines, schema=_PRODUCT_SCHEMA,
        )
        if not data:
            return None
        by_i = {r["i"]: r for r in data["results"]}
        out += [by_i.get(i, {"product_name": None, "is_physical_product": True, "category": "other"}) for i in range(len(chunk))]
    return out
