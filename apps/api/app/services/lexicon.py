"""Multilingual product lexicon — one concept per row: English key → same thing in en / vi / tl / ar / zh, then `related`
(near neighbours MKT also wants to see: "vòng tay phong thủy" → lucky / pixiu bracelets).

Used by
  naming.py   a name taken from ad copy must contain a product noun (TYPES) — "Firm Body or Money Back" has none
  search      query → concepts → exact / synonym / related variants (intel.search)

ponytail: hand-made, ~80 concepts around what the team sells (jewelry & feng shui, beauty, health, home, gadgets,
fashion — PH / GCC / VN). Add a row when a search misses; no model, no API.
"""
from __future__ import annotations

import re
import unicodedata

# kind T = product type (counts as a product noun), M = modifier (material / style / benefit)
CONCEPTS: dict[str, tuple[str, list[str], list[str]]] = {
    # ---- jewelry & feng shui
    "bracelet": ("T", ["bracelet", "bracelets", "bangle", "bangles", "vòng tay", "lắc tay", "vòng đeo tay", "pulseras", "pulsera",
                       "سوار", "أساور", "اسوارة", "手链", "手镯", "手串"], ["beads bracelet", "charm bracelet"]),
    "necklace": ("T", ["necklace", "necklaces", "dây chuyền", "vòng cổ", "kwintas", "قلادة", "سلسال", "项链"], ["pendant", "chain"]),
    "ring": ("T", ["ring", "rings", "nhẫn", "singsing", "خاتم", "خواتم", "戒指"], []),
    "earring": ("T", ["earring", "earrings", "bông tai", "khuyên tai", "hikaw", "أقراط", "حلق", "耳环", "耳钉"], []),
    "pendant": ("T", ["pendant", "mặt dây chuyền", "palawit", "تعليقة", "吊坠"], ["necklace", "amulet"]),
    "amulet": ("T", ["amulet", "talisman", "charm", "bùa", "bùa hộ mệnh", "anting-anting", "agimat", "تميمة", "护身符", "平安符"], ["pendant", "lucky"]),
    "anklet": ("T", ["anklet", "lắc chân", "vòng chân", "خلخال", "脚链"], []),
    "jewelry": ("T", ["jewelry", "jewellery", "trang sức", "alahas", "مجوهرات", "إكسسوارات", "首饰", "饰品"], ["bracelet", "necklace", "ring", "earring"]),
    "feng shui": ("M", ["feng shui", "fengshui", "phong thủy", "phong thuỷ", "فنغ شوي", "风水"], ["lucky", "pixiu", "wealth", "crystal", "amulet"]),
    "lucky": ("M", ["lucky", "good luck", "luck", "may mắn", "swerte", "suwerte", "حظ", "幸运", "好运", "转运"], ["feng shui", "wealth", "amulet"]),
    # not "money" / "fortune": they matched "money back guarantee", "Fortune Group"
    "wealth": ("M", ["wealth", "prosperity", "tài lộc", "chiêu tài", "phát tài", "yaman", "ثروة", "招财", "聚宝盆"], ["lucky", "feng shui"]),
    "pixiu": ("T", ["pixiu", "piyao", "pi xiu", "tỳ hưu", "tì hưu", "貔貅"], ["feng shui", "wealth"]),
    "money toad": ("T", ["money toad", "golden toad", "money frog", "cóc ngậm tiền", "cóc tài lộc", "金蟾"], ["feng shui", "wealth"]),
    "crystal": ("T", ["crystal", "crystals", "quartz", "đá phong thủy", "thạch anh", "kristal", "كريستال", "水晶"], ["feng shui", "beads"]),
    "beads": ("T", ["beads", "bead", "chuỗi hạt", "tràng hạt", "prayer beads", "mala", "سبحة", "مسبحة", "佛珠", "串珠"], ["bracelet"]),
    "figurine": ("T", ["figurine", "statue", "ornament", "sculpture", "tượng", "linh vật", "rebulto", "تمثال", "摆件", "雕像"], ["feng shui"]),
    "keychain": ("T", ["keychain", "key chain", "key ring", "móc khóa", "móc chìa khóa", "ميدالية", "钥匙扣"], []),
    "jade": ("M", ["jade", "ngọc bích", "cẩm thạch", "يشم", "玉", "和田玉"], ["crystal"]),
    "gold": ("M", ["gold", "gold plated", "vàng", "mạ vàng", "ginto", "ذهب", "ذهبي", "金", "黄金"], []),
    "silver": ("M", ["silver", "sterling", "bạc", "pilak", "فضة", "银"], []),
    # ---- beauty
    "serum": ("T", ["serum", "essence", "ampoule", "tinh chất", "سيروم", "精华", "精华液"], ["cream", "toner"]),
    "cream": ("T", ["cream", "moisturizer", "kem", "kem dưỡng", "krema", "كريم", "面霜", "霜"], ["serum", "lotion"]),
    "lotion": ("T", ["lotion", "body lotion", "sữa dưỡng thể", "لوشن", "身体乳"], ["cream"]),
    "toner": ("T", ["toner", "nước hoa hồng", "تونر", "爽肤水"], ["serum"]),
    "sunscreen": ("T", ["sunscreen", "sunblock", "spf", "kem chống nắng", "واقي شمس", "防晒"], ["cream"]),
    "face mask": ("T", ["face mask", "sheet mask", "mask", "mặt nạ", "ماسك", "面膜"], []),
    "lipstick": ("T", ["lipstick", "lip tint", "liptint", "lip gloss", "lip balm", "son", "son môi", "son kem", "أحمر شفاه", "口红", "唇釉"], []),
    "foundation": ("T", ["foundation", "cushion", "kem nền", "phấn nước", "كريم أساس", "粉底"], []),
    "perfume": ("T", ["perfume", "fragrance", "parfum", "cologne", "nước hoa", "pabango", "عطر", "عطور", "香水"], ["oud"]),
    "oud": ("T", ["oud", "bakhoor", "bukhoor", "incense", "trầm hương", "عود", "بخور", "دهن عود", "沉香"], ["perfume"]),
    "soap": ("T", ["soap", "body wash", "xà phòng", "xà bông", "sữa tắm", "sabon", "صابون", "香皂", "沐浴露"], []),
    "shampoo": ("T", ["shampoo", "conditioner", "dầu gội", "شامبو", "洗发水"], ["hair oil"]),
    "hair oil": ("T", ["hair oil", "hair serum", "hair growth", "dầu dưỡng tóc", "mọc tóc", "زيت شعر", "生发"], ["shampoo"]),
    "hair removal": ("T", ["hair removal", "epilator", "wax", "waxing", "ipl", "tẩy lông", "wax lông", "إزالة الشعر", "脱毛"], []),
    "teeth whitening": ("T", ["teeth whitening", "whitening strips", "toothpaste", "làm trắng răng", "kem đánh răng", "تبييض الأسنان", "牙贴", "牙膏"], []),
    "mist": ("T", ["mist", "spray", "xịt", "xịt khoáng", "بخاخ", "喷雾"], ["toner"]),
    "wig": ("T", ["wig", "hair extension", "tóc giả", "peluka", "شعر مستعار", "باروكة", "假发"], []),
    # ---- health
    "massager": ("T", ["massager", "massage", "massage device", "máy massage", "máy mát xa", "masahe", "مدلك", "مساج", "按摩", "按摩器"], ["massage gun"]),
    "massage chair": ("T", ["massage chair", "ghế massage", "ghế mát xa", "كرسي مساج", "按摩椅"], ["massager"]),
    "comb": ("T", ["comb", "hair brush", "lược", "suklay", "مشط", "梳子"], []),
    "massage gun": ("T", ["massage gun", "súng massage", "مسدس مساج", "筋膜枪"], ["massager"]),
    "posture corrector": ("T", ["posture corrector", "posture brace", "back brace", "back support", "đai chống gù", "đai lưng",
                                "مصحح القوام", "مشد الظهر", "矫姿带", "背背佳"], ["brace"]),
    "brace": ("T", ["brace", "knee brace", "knee support", "knee pad", "bó gối", "băng gối", "đai", "دعامة", "ركبة", "护膝", "护腰"], ["posture corrector"]),
    "pillow": ("T", ["pillow", "cushion", "gối", "unan", "وسادة", "مخدة", "枕头"], []),
    "insoles": ("T", ["insole", "insoles", "lót giày", "نعل طبي", "鞋垫"], []),
    "patch": ("T", ["patch", "patches", "plaster", "miếng dán", "لصقة", "贴"], []),
    "gummies": ("T", ["gummies", "gummy", "kẹo dẻo", "حلوى", "软糖"], []),
    "honey": ("T", ["honey", "mật ong", "pulot", "عسل", "蜂蜜"], []),
    # ---- home
    "vacuum": ("T", ["vacuum", "vacuum cleaner", "máy hút bụi", "مكنسة", "吸尘器"], ["mop"]),
    "mop": ("T", ["mop", "cây lau nhà", "lampaso", "ممسحة", "拖把"], ["vacuum"]),
    "blender": ("T", ["blender", "juicer", "máy xay", "máy ép", "خلاط", "عصارة", "榨汁机", "搅拌机"], []),
    "lamp": ("T", ["lamp", "light", "led", "night light", "đèn", "ilaw", "مصباح", "إضاءة", "灯"], []),
    "fan": ("T", ["fan", "mini fan", "quạt", "bentilador", "مروحة", "风扇"], []),
    "printer": ("T", ["printer", "thermal printer", "máy in", "طابعة", "打印机"], []),
    "sprayer": ("T", ["sprayer", "water sprayer", "pressure washer", "máy rửa xe", "bình xịt", "رشاش", "喷枪"], []),
    "organizer": ("T", ["organizer", "storage", "kệ", "hộp đựng", "lalagyan", "منظم", "收纳"], []),
    "kitchen tool": ("T", ["knife", "pan", "pot", "kettle", "cutter", "dao", "chảo", "nồi", "ấm", "kutsilyo", "kawali", "سكين", "مقلاة", "قدر", "锅", "刀"], []),
    "cleaner": ("T", ["cleaner", "cleaning", "detergent", "nước tẩy", "chất tẩy", "panlinis", "منظف", "清洁剂"], []),
    # ---- gadgets
    "headphones": ("T", ["headphones", "headphone", "earbuds", "earphones", "tai nghe", "سماعة", "سماعات", "耳机"], []),
    "charger": ("T", ["charger", "power bank", "powerbank", "sạc", "sạc dự phòng", "شاحن", "充电宝", "充电器"], []),
    "phone case": ("T", ["phone case", "case", "ốp lưng", "ốp điện thoại", "جراب", "手机壳"], []),
    "phone holder": ("T", ["phone holder", "car holder", "phone stand", "giá đỡ điện thoại", "حامل جوال", "手机支架"], []),
    "stylus": ("T", ["stylus", "stylus pen", "bút cảm ứng", "قلم لمس", "触控笔"], []),
    "smartwatch": ("T", ["smartwatch", "smart watch", "đồng hồ thông minh", "ساعة ذكية", "智能手表"], ["watch"]),
    "camera": ("T", ["camera", "cctv", "dashcam", "máy ảnh", "kamera", "كاميرا", "摄像头"], []),
    "projector": ("T", ["projector", "máy chiếu", "بروجكتر", "投影仪"], []),
    "speaker": ("T", ["speaker", "loa", "مكبر صوت", "音箱"], []),
    # ---- fashion
    "watch": ("T", ["watch", "watches", "đồng hồ", "relo", "ساعة", "手表"], ["smartwatch"]),
    "wallet": ("T", ["wallet", "purse", "ví", "pitaka", "محفظة", "钱包"], ["bag"]),
    "bag": ("T", ["bag", "handbag", "backpack", "túi", "túi xách", "balo", "bayong", "حقيبة", "شنطة", "包", "背包"], ["wallet"]),
    "shoes": ("T", ["shoes", "sneakers", "sandals", "slippers", "giày", "dép", "sapatos", "tsinelas", "حذاء", "أحذية", "鞋"], ["insoles"]),
    "dress": ("T", ["dress", "gown", "đầm", "váy", "damit", "bestida", "فستان", "فساتين", "连衣裙"], ["abaya"]),
    "abaya": ("T", ["abaya", "jalabiya", "kaftan", "عباية", "عبايات", "جلابية"], ["dress"]),
    "bra": ("T", ["bra", "bralette", "áo ngực", "áo lót", "حمالة صدر", "文胸", "内衣"], ["shapewear"]),
    "shapewear": ("T", ["shapewear", "waist trainer", "body shaper", "gen nịt bụng", "đai nịt", "مشد", "塑身衣"], ["bra"]),
    "eyeglasses": ("T", ["eyeglasses", "eyeglass", "glasses", "spectacles", "optical frame", "eyewear", "kính mắt", "kính cận", "kính cận thị",
                         "kính thời trang", "gọng kính", "kính đọc sách", "salamin sa mata", "gafas", "lentes", "anteojos",
                         "نظارة", "نظارات", "نظارة طبية", "نظارات طبية", "眼镜", "近视眼镜", "老花镜"], ["sunglasses", "blue light glasses"]),
    "blue light glasses": ("T", ["blue light glasses", "blue blocker", "anti blue light", "kính chống ánh sáng xanh", "kính chống tia xanh",
                                  "نظارة الضوء الأزرق", "防蓝光眼镜"], ["eyeglasses"]),
    "sunglasses": ("T", ["sunglasses", "sunglass", "shades", "kính mát", "kính râm", "gafas de sol", "salamin pang-araw", "نظارة شمسية",
                          "نظارات شمسية", "墨镜", "太阳镜"], ["eyeglasses"]),
    "hat": ("T", ["hat", "cap", "mũ", "nón", "sumbrero", "قبعة", "帽子"], []),
    "socks": ("T", ["socks", "tất", "vớ", "medyas", "جوارب", "袜子"], []),
    "gloves": ("T", ["gloves", "glove", "găng tay", "guwantes", "قفازات", "手套"], []),
    # ---- kids & pets
    "toy": ("T", ["toy", "toys", "đồ chơi", "laruan", "لعبة", "ألعاب", "玩具"], []),
    "pet": ("M", ["pet", "dog", "cat", "thú cưng", "chó", "mèo", "aso", "pusa", "قطة", "كلب", "宠物"], []),
    "leash": ("T", ["leash", "harness", "dây dắt", "tali", "مقود", "牵引绳"], ["pet"]),
    "litter": ("T", ["litter", "cát vệ sinh", "رمل قطط", "猫砂"], ["pet"]),
    "planner": ("T", ["planner", "journal", "notebook", "sổ tay", "sổ kế hoạch", "دفتر", "笔记本"], []),
}


def fold(s: str) -> str:
    """lower-case, Vietnamese tones kept (they change the word), spacing / punctuation squashed."""
    s = unicodedata.normalize("NFC", (s or "").lower())
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s'-]", " ", s)).strip()


_TERM: dict[str, str] = {}  # every term (folded) → concept key
for _k, (_kind, _terms, _rel) in CONCEPTS.items():
    for _t in [_k, *_terms]:
        _TERM.setdefault(fold(_t), _k)
_MAXW = max(len(t.split()) for t in _TERM)
_CJK = re.compile(r"[㐀-鿿]")
# ponytail: CJK terms are matched as substrings (no spaces in Chinese); short Latin ones need word boundaries
_CJK_TERMS = sorted((t for t in _TERM if _CJK.search(t)), key=len, reverse=True)


def concepts_in(text: str) -> list[tuple[str, str]]:
    """[(matched term, concept)] in reading order, longest phrase first ("knee brace" before "brace")."""
    words, out, i = fold(text).split(), [], 0
    while i < len(words):
        for n in range(min(_MAXW, len(words) - i), 0, -1):
            t = " ".join(words[i:i + n])
            if t in _TERM:
                out.append((t, _TERM[t]))
                i += n
                break
        else:
            i += 1
    if _CJK.search(text or ""):
        rest = text
        for t in _CJK_TERMS:
            if t in rest:
                out.append((t, _TERM[t]))
                rest = rest.replace(t, " ")
    return out


# terms that name a product only next to another noun: "Full Body Massage" is a spa, "Light Luxury" a style
WEAK = {"massage", "support", "light", "led", "case", "storage", "charm", "money", "wax", "spray", "mist", "mask", "cushion",
        "đai", "kệ", "cap", "luck", "fortune", "son", "loa", "spf", "pot", "pan", "chain", "ساعة"}  # ساعة = watch / hour


def has_product_noun(text: str) -> bool:
    return any(CONCEPTS[c][0] == "T" and t not in WEAK for t, c in concepts_in(text))


# a different product whose words contain this one's ("sunglasses" ⊃ "glasses"): an ad naming it is a related hit, not the same thing
CONFUSABLE: dict[str, list[str]] = {"eyeglasses": ["sunglasses"]}


def variants(concept: str, related: bool = False) -> list[str]:
    kind, terms, rel = CONCEPTS[concept]
    out = [concept, *terms]
    if related:
        for r in rel:
            out += [r, *CONCEPTS[r][1][:4]] if r in CONCEPTS else [r]
    return list(dict.fromkeys(fold(t) for t in out))


def expand(query: str) -> dict:
    """'vòng tay phong thủy' → {"groups": [[bracelet terms…], [feng shui terms…]], "related": [[…+lucky, pixiu…], …],
    "rest": [words outside the lexicon]}. A match = every group hit (AND), any term of a group (OR)."""
    found = concepts_in(query)
    seen, groups, related, avoid = set(), [], [], []
    for _, c in found:
        if c in seen:
            continue
        seen.add(c)
        groups.append(variants(c))
        avoid.append([v for a in CONFUSABLE.get(c, []) for v in variants(a)])
        related.append(variants(c, related=True))
    rest_text = f" {fold(query)} "
    for t, _ in found:  # CJK has no spaces: cut the matched terms out of the text, not out of a word list
        rest_text = rest_text.replace(f" {t} ", " ") if not _CJK.search(t) else rest_text.replace(t, " ")
    rest = [w for w in rest_text.split() if len(w) > 1]
    return {"groups": groups, "related": related, "avoid": avoid, "rest": rest, "concepts": list(seen)}


def english_queries(query: str, n: int = 3) -> list[str]:
    """Source-language keywords for a live (external) search: 'vòng tay phong thủy' → ['feng shui bracelet',
    'lucky bracelet', …]. Modifiers before the product type; first the exact concepts, then one related swap at a time.
    Capped: every extra keyword is a full scan of every paid / rate-limited source."""
    hits = concepts_in(query)
    found = list(dict.fromkeys(c for _, c in hits))
    if not found:
        return []
    own = {c: t for t, c in hits if t.isascii()}  # the user's own English words win ("knee brace", not "brace")
    order = lambda cs: " ".join(own.get(c, c) for c in sorted(cs, key=lambda c: CONCEPTS[c][0] != "M"))  # modifier first
    out = [order(found)]
    for i, c in enumerate(found):
        for r in CONCEPTS[c][2]:
            if r in CONCEPTS and r not in found and CONCEPTS[r][0] == CONCEPTS[c][0]:
                out.append(order(found[:i] + [r] + found[i + 1:]))
    rest = [w for w in expand(query)["rest"] if len(w) > 2]
    out = [" ".join([q, *rest]) for q in dict.fromkeys(out) if fold(q) != fold(query)]
    return out[:n]


if __name__ == "__main__":
    assert english_queries("Vòng tay phong thủy")[:2] == ["feng shui bracelet", "lucky bracelet"], english_queries("Vòng tay phong thủy")
    e = expand("Vòng tay phong thủy")
    assert {"bracelet", "feng shui"} == set(e["concepts"]) and not e["rest"], e
    assert "lucky" in e["related"][1] and "vòng tay" in e["groups"][0]
    assert has_product_noun("XỊT DƯỠNG CĂNG BÓNG CICA PDRN SERUM MIST") and not has_product_noun("Firm Body or Money Back")
    assert [c for _, c in concepts_in("knee brace for men")] == ["brace"]
    assert concepts_in("风水貔貅手链")[0][1] in ("pixiu", "bracelet", "feng shui") and len(concepts_in("风水貔貅手链")) == 3
    print("ok")
