"""AI Agent (blueprint Phase 10): natural-language questions → structured query → database.

The LLM (if configured) only translates the question into a query spec and phrases
the answer; all numbers come from the database.
"""
import json
import re
from collections import Counter
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import ADS_ONLY, Ad, Advertiser, Comment, MarketDailySnapshot, Product
from . import llm
from .engine import is_hidden_winner

INTENTS = ["product_search", "top_complaints", "market_hidden_winners", "competitors_scaling", "explain_product"]

COUNTRY_WORDS = {
    "SA": ["saudi", "ả rập", "a rap", "ksa", "ả-rập"], "AE": ["uae", "dubai", "emirates", "các tiểu vương quốc"],
    "KW": ["kuwait"], "QA": ["qatar"], "VN": ["việt nam", "vietnam", "vn"], "TH": ["thái", "thailand", "thai"],
    "PH": ["philippines", "phi"], "MY": ["malaysia", "mã lai"], "ID": ["indonesia", "indo"], "US": ["mỹ", "usa", "united states"],
}
CATEGORY_WORDS = {
    "beauty": ["beauty", "làm đẹp", "mỹ phẩm", "skincare"], "health": ["health", "sức khỏe", "massage"],
    "home": ["home", "gia dụng", "nhà cửa", "kitchen"], "gadgets": ["gadget", "công nghệ", "điện tử", "tech"],
    "fashion": ["fashion", "thời trang"], "pets": ["pet", "thú cưng"],
}
SPEC_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": INTENTS},
        "country": {"type": ["string", "null"], "description": "ISO-2 country code"},
        "category": {"type": ["string", "null"], "enum": ["beauty", "health", "home", "gadgets", "fashion", "pets", None]},
        "max_advertisers": {"type": ["integer", "null"]},
        "min_win_score": {"type": ["number", "null"]},
        "fast_growing": {"type": "boolean"},
        "hidden_winner": {"type": "boolean"},
        "days": {"type": ["integer", "null"]},
        "product_query": {"type": ["string", "null"]},
        "limit": {"type": "integer"},
    },
    "required": ["intent", "country", "category", "max_advertisers", "min_win_score",
                 "fast_growing", "hidden_winner", "days", "product_query", "limit"],
    "additionalProperties": False,
}


def _find(text: str, table: dict) -> str | None:
    for key, words in table.items():
        for w in words:
            if re.search(rf"(?<!\w){re.escape(w)}(?!\w)", text):
                return key
    return None


def parse_rules(q: str) -> dict:
    t = q.lower()
    spec = {k: None for k in SPEC_SCHEMA["properties"]}
    spec.update({"fast_growing": False, "hidden_winner": False, "limit": 20})
    m = re.search(r"(?:top|tìm|find|cho tôi)\s*(\d+)", t)
    if m:
        spec["limit"] = min(100, int(m.group(1)))
    spec["country"] = _find(t, COUNTRY_WORDS) or ("US" if re.search(r"\bUS\b", q) else None)  # "us" = we, "US" = the market
    spec["category"] = _find(t, CATEGORY_WORDS)
    m = re.search(r"(?:dưới|<|less than|under|ít hơn)\s*(\d+)\s*(?:advertiser|nhà quảng cáo|adv)", t)
    if m:
        spec["max_advertisers"] = int(m.group(1))
    m = re.search(r"(\d+)\s*ngày|(\d+)\s*days", t)
    if m:
        spec["days"] = int(m.group(1) or m.group(2))
    spec["fast_growing"] = bool(re.search(r"tăng nhanh|tăng trưởng|fast grow|growing|đang tăng", t))
    if re.search(r"win score cao|high win", t):
        spec["min_win_score"] = 65

    if re.search(r"complaint|phàn nàn|khiếu nại", t):
        spec["intent"] = "top_complaints"
        m = re.search(r"(?:sản phẩm|product)\s+(.+?)[\.\?\"”]*$", q.strip(), re.IGNORECASE)
        spec["product_query"] = m.group(1).strip(" \"'“”.?") if m else None
    elif re.search(r"market nào|thị trường nào|which market", t):
        spec["intent"] = "market_hidden_winners"
        spec["days"] = spec["days"] or 7
    elif re.search(r"đối thủ|competitor|advertiser nào", t) and re.search(r"scale|tăng|nhanh", t):
        spec["intent"] = "competitors_scaling"
    elif re.search(r"giải thích|vì sao|why|explain", t):
        spec["intent"] = "explain_product"
        m = re.search(r"(PRD_\d+)", q, re.IGNORECASE)
        spec["product_query"] = m.group(1).upper() if m else q
    else:
        spec["intent"] = "product_search"
        spec["hidden_winner"] = bool(re.search(r"hidden|hiếm|rare|ít người chạy", t))
    return spec


def parse(q: str) -> tuple[dict, str]:
    if llm.available():
        spec = llm.complete_json(
            system=(
                "Translate the user's market-intelligence question (Vietnamese or English) into a query spec for a "
                "product database. Intents: product_search (find products by filters), top_complaints (complaints of one product — put "
                "its name/code in product_query), market_hidden_winners (which market has most hidden winners), "
                "competitors_scaling (advertisers scaling fast), explain_product (why a product has its score). "
                "Use null for filters not mentioned. limit defaults to 20."
            ),
            user=q, schema=SPEC_SCHEMA,
        )
        if spec:
            return spec, "claude"
    return parse_rules(q), "rules"


def _product_row(p: Product) -> dict:
    f = p.features or {}
    return {
        "id": p.id, "product_code": p.product_code, "name": p.canonical_name, "country": p.country, "category": p.category,
        "advertisers": f.get("advertiser_count"), "active_ads": f.get("active_ads"),
        "growth_7d": f.get("creative_growth_7d"), "win": p.win_score, "rarity": p.rarity_score,
        "saturation": p.saturation_score, "opportunity": p.opportunity_score, "confidence": p.confidence_score,
        "recommendation": p.recommendation,
    }


def _find_product(db: Session, q: str | None) -> Product | None:
    if not q:
        return None
    p = db.scalar(select(Product).where(Product.product_code == q.upper()))
    if p:
        return p
    from .entity_resolution import find_best_match
    p, s = find_best_match(db, {"name": q})
    return p if s >= 0.35 else db.scalar(select(Product).where(Product.canonical_name.ilike(f"%{q}%")))


def execute(db: Session, spec: dict) -> dict:
    intent = spec["intent"]
    limit = spec.get("limit") or 20
    if intent == "product_search":
        rows = []
        for p in db.scalars(select(Product)).all():
            f = p.features or {}
            if spec.get("country") and spec["country"] not in (f.get("markets") or [p.country]):
                continue
            if spec.get("category") and p.category != spec["category"]:
                continue
            if spec.get("max_advertisers") is not None and f.get("advertiser_count", 0) >= spec["max_advertisers"]:
                continue
            if spec.get("min_win_score") is not None and p.win_score < spec["min_win_score"]:
                continue
            if spec.get("fast_growing") and f.get("creative_growth_7d", 0) < 0.3:
                continue
            if spec.get("hidden_winner") and not is_hidden_winner(p):
                continue
            if spec.get("days") and p.last_seen_at < datetime.utcnow() - timedelta(days=spec["days"]):
                continue
            rows.append(p)
        key = (lambda p: p.features.get("creative_growth_7d", 0)) if spec.get("fast_growing") else (lambda p: p.opportunity_score)
        rows.sort(key=key, reverse=True)
        return {"kind": "products", "rows": [_product_row(p) for p in rows[:limit]], "total": len(rows)}

    if intent == "top_complaints":
        p = _find_product(db, spec.get("product_query"))
        if not p:
            return {"kind": "message", "rows": [], "message": "Không tìm thấy sản phẩm."}
        cs = db.scalars(select(Comment).where(Comment.product_id == p.id)).all()
        neg = Counter(a for c in cs for a, v in (c.aspects or {}).items() if v == "negative")
        total = sum(neg.values()) or 1
        examples = {}
        for c in cs:
            for a, v in (c.aspects or {}).items():
                if v == "negative" and a not in examples:
                    examples[a] = c.text
        rows = [{"aspect": a, "count": n, "share": round(n / total, 3), "example": examples.get(a)} for a, n in neg.most_common(limit)]
        return {"kind": "complaints", "product": _product_row(p), "rows": rows, "total": len(cs)}

    if intent == "market_hidden_winners":
        last = db.scalar(select(func.max(MarketDailySnapshot.date)))
        rows = []
        if last:
            since = last - timedelta(days=(spec.get("days") or 7) - 1)
            data = db.execute(
                select(MarketDailySnapshot.country, func.max(MarketDailySnapshot.hidden_winners).label("hw"))
                .where(MarketDailySnapshot.date >= since).group_by(MarketDailySnapshot.country, MarketDailySnapshot.category)
            ).all()
            agg = Counter()
            for country, hw in data:
                agg[country] += hw or 0
            rows = [{"country": c, "hidden_winners": n} for c, n in agg.most_common(limit)]
        return {"kind": "markets", "rows": rows, "total": len(rows)}

    if intent == "competitors_scaling":
        now = datetime.utcnow()
        data = db.execute(
            select(Ad.advertiser_id, func.count(Ad.id)).where(Ad.first_seen_at > now - timedelta(days=7), Ad.is_internal.is_(False), ADS_ONLY,
                                                               Ad.advertiser_id.is_not(None)).group_by(Ad.advertiser_id)
        ).all()
        rows = []
        for adv_id, n7 in data:
            prev = db.scalar(select(func.count(Ad.id)).where(Ad.advertiser_id == adv_id, Ad.first_seen_at <= now - timedelta(days=7),
                                                             Ad.first_seen_at > now - timedelta(days=14)))
            adv = db.get(Advertiser, adv_id)
            rows.append({"advertiser_id": adv_id, "advertiser": adv.name, "country": adv.country, "new_ads_7d": n7,
                         "prev_7d": prev, "growth": round(n7 / max(prev, 1) - 1, 2)})
        rows.sort(key=lambda r: (r["new_ads_7d"] - r["prev_7d"]), reverse=True)
        return {"kind": "competitors", "rows": rows[:limit], "total": len(rows)}

    if intent == "explain_product":
        p = _find_product(db, spec.get("product_query"))
        if not p:
            return {"kind": "message", "rows": [], "message": "Không tìm thấy sản phẩm."}
        return {"kind": "explain", "product": _product_row(p), "rows": [], "explanation": explain_product(p)}
    return {"kind": "message", "rows": [], "message": "Chưa hỗ trợ câu hỏi này."}


def template_answer(spec: dict, result: dict) -> str:
    k, n = result["kind"], result.get("total", 0)
    if k == "products":
        return f"Tìm thấy {n} sản phẩm phù hợp; hiển thị {len(result['rows'])} sản phẩm theo {'tăng trưởng 7d' if spec.get('fast_growing') else 'Opportunity'}."
    if k == "complaints":
        top = result["rows"][0] if result["rows"] else None
        return (f"{result['product']['name']}: {n} comment; phàn nàn nhiều nhất là '{top['aspect']}' ({top['share']:.0%})."
                if top else f"{result['product']['name']}: chưa có phàn nàn.")
    if k == "markets":
        top = result["rows"][0] if result["rows"] else None
        return f"Market có nhiều hidden winner nhất: {top['country']} ({top['hidden_winners']})." if top else "Chưa có snapshot."
    if k == "competitors":
        top = result["rows"][0] if result["rows"] else None
        return f"Đối thủ scale nhanh nhất: {top['advertiser']} (+{top['new_ads_7d']} ads 7d, tuần trước {top['prev_7d']})." if top else "Không có dữ liệu."
    if k == "explain":
        return result["explanation"]
    return result.get("message", "")


def ask(db: Session, question: str) -> dict:
    spec, engine = parse(question)
    result = execute(db, spec)
    answer = template_answer(spec, result)
    if llm.available() and result["kind"] not in ("message", "explain") and result["rows"]:
        summary = llm.complete_text(
            system="Bạn là analyst. Trả lời ngắn gọn bằng tiếng Việt (3-6 câu) dựa CHỈ trên dữ liệu JSON. Không bịa số.",
            user=f"Câu hỏi: {question}\nDữ liệu: {json.dumps(result['rows'][:20], ensure_ascii=False, default=str)}",
        )
        answer = summary or answer
    return {"question": question, "spec": spec, "engine": engine, "answer": answer, **result}


# ------------------------------------------------------------ Score explanation (§20)
def explain_product(p: Product) -> str:
    f = p.features or {}
    b = f.get("_breakdown", {})
    ext = b.get("external", {})
    tops = sorted(((k, v["value"] * v["weight"]) for k, v in ext.items() if v["weight"] > 0), key=lambda x: -x[1])
    weak = sorted(((k, v["value"]) for k, v in ext.items() if v["weight"] > 0), key=lambda x: x[1])[:2]
    strong = ", ".join(f"{k} ({ext[k]['value']:.0f})" for k, _ in tops[:3])
    weak_s = ", ".join(f"{k} ({v:.0f})" for k, v in weak)
    reasons = "; ".join(p.recommendation_reasons or [])
    base = (
        f"{p.canonical_name} có Win Score {p.win_score:.0f} (External {p.external_win_score:.0f}). "
        f"Đóng góp lớn nhất: {strong}. Điểm yếu: {weak_s}. "
        f"Saturation {p.saturation_score:.0f} ({p.saturation_state}), Rarity {p.rarity_score:.0f}, "
        f"Confidence {p.confidence_score:.0f} → Opportunity {p.opportunity_score:.0f}. "
        f"Khuyến nghị: {p.recommendation} ({reasons})."
    )
    if llm.available():
        text = llm.complete_text(
            system=("Bạn giải thích điểm số sản phẩm cho team marketing bằng tiếng Việt. Điểm số đã được tính bằng công thức; "
                    "KHÔNG được thay đổi hay tự chấm điểm, chỉ giải thích vì sao ra con số đó và rủi ro cần lưu ý. Tối đa 150 từ."),
            user=json.dumps({"product": p.canonical_name, "scores": {
                "win": p.win_score, "external": p.external_win_score,
                "saturation": p.saturation_score, "rarity": p.rarity_score, "confidence": p.confidence_score,
                "opportunity": p.opportunity_score, "recommendation": p.recommendation},
                "breakdown": b, "features": {k: v for k, v in f.items() if not k.startswith("_")}}, ensure_ascii=False, default=str),
        )
        if text:
            return text
    return base
