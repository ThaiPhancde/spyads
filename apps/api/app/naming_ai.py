"""Product naming queue: evidence first, AI only for what evidence cannot name, never over a MKT name.

Pass 1 — rules (no AI, every tick, cheap): pricing/naming_hints.candidates() reads what the product's own data says — page identity /
  schema.org (JSON-LD) metadata of the landing page, shop-page slug, listing title, caption / headline (naming.best), OCR of the thumbnail,
  a named near-duplicate product (CLIP), Lens titles. A candidate from a trustworthy source names the product (AUTO_CONFIRMED ≥ 0.85, else
  SUGGESTED for MKT to confirm). Checkpoint: products.naming_checked_at.
Pass 2 — AI (batches of 10–30 products per request, sized from the provider's live token headroom): GroqCloud gpt-oss-20b, then Cerebras,
  Cloudflare Workers AI, Gemini (llm.py chain). The model must name the product TYPE in English from the ad's own words, say how sure it is
  and which words prove it. An answer is accepted only when it is grounded in the ad (naming.grounded), is not a shop / CTA / advertiser
  (naming.is_bad_name), and is confident; a weak answer is kept as a low-confidence hint and the product goes to MKT review
  (name_source = "needs_review"); no product at all → "llm_none". Checkpoint: name_source + naming_attempts (≤ MAX_ATTEMPTS on errors).
Never touched: manual_override / VERIFIED products, products already named. A name that already exists merges into that product.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime

from sqlalchemy import func, or_, select

from .models import Ad, Advertiser, Creative, Product
from .services import enrichment as E
from .services import lexicon, llm
from .services import naming as N
from .services.entity_resolution import MATCH_THRESHOLD, find_best_match, merge_products

log = logging.getLogger("app.naming_ai")
MAX_ATTEMPTS = 3
MIN_BATCH, MAX_BATCH = 10, 30
ACCEPT_CONF = float(os.getenv("NAMING_AI_ACCEPT", "0.7"))
RULE_ACCEPT = 0.8          # a rules candidate below this is only a hint
# naming_hints source → (naming source label, cap on confidence): a near-duplicate product's name / Lens titles are never more than a suggestion
RULE_SOURCES = {"identity": ("listing", 0.95), "evidence": ("slug", 0.9), "metadata": ("listing", 0.85), "caption": ("ad_title", 0.8),
                "ocr": ("ad_title", 0.65), "clip": ("legacy", 0.7), "lens": ("legacy", 0.7)}
DONE_SOURCES = ("llm_none", "needs_review")


def _env_int(name: str, default: int) -> int:
    try:
        return int((os.getenv(name) or str(default)).split("#")[0].strip())
    except ValueError:
        return default


def _pending_rules(db, limit: int):
    return db.scalars(select(Product).where(Product.name_status == "UNKNOWN", Product.naming_checked_at.is_(None),
                                            or_(Product.manual_override.is_(None), Product.manual_override.is_(False)))
                      .order_by(Product.in_target.desc().nullslast(), Product.opportunity_score.desc(), Product.id.desc()).limit(limit)).all()


def rule_pass(db, limit: int = 300) -> dict:
    """Name UNKNOWN products from their own evidence, no AI. Every product is looked at once (naming_checked_at)."""
    from .pricing.naming_hints import candidates

    out = {"checked": 0, "named": 0, "hinted": 0}
    for p in _pending_rules(db, limit):
        ads = list(db.scalars(select(Ad).where(Ad.product_id == p.id).limit(10)))
        try:
            cands = candidates(db, p, ads)
        except Exception as e:  # noqa: BLE001 - one product's odd data never stops the pass
            log.warning("rules for product %s: %s", p.id, str(e)[:120])
            cands = []
        p.naming_checked_at = datetime.utcnow()
        out["checked"] += 1
        best = cands[0] if cands else None
        if not best:
            continue
        label, cap = RULE_SOURCES.get(best["source"], ("legacy", 0.6))
        conf = min(cap, best["confidence"] + 0.1 * best.get("agreed_by", 0))
        if conf >= RULE_ACCEPT and best["source"] not in ("clip", "lens"):
            nm = {"name": best["name"], "source": label, "confidence": round(conf, 2), "status": "AUTO_CONFIRMED" if conf >= N.AUTO_CONFIRM_AT else "SUGGESTED",
                  "evidence": [{"source": label, "value": best["name"], "via": best["source"]}]}
            _name(db, p, ads, nm)
            out["named"] += 1
        else:  # not enough to name it: a hint for MKT, the AI pass still gets the product
            N._add_evidence(p, [{"source": "rule_hint", "value": best["name"], "via": best["source"], "confidence": round(conf, 2)}])
            out["hinted"] += 1
    db.flush()
    return out


def _name(db, p: Product, ads: list[Ad], nm: dict) -> int:
    """Rename p (or merge it into the existing product of that name). Returns the surviving product id."""
    target, score = find_best_match(db, {"name": nm["name"], "category": p.category})
    if target is not None and score >= MATCH_THRESHOLD and target.id != p.id and target.name_status != "UNKNOWN":
        merge_products(db, p, target, by="naming")
        N.absorb(target, nm, nm["evidence"])
        survivor = target
    else:
        N.absorb(p, nm, nm["evidence"])  # UNKNOWN ranks lowest: always renamed; a MKT name is never touched (absorb)
        p.category = p.category or E.classify_category(nm["name"], ads[0].ad_text if ads else None)
        survivor = p
    for x in ads:
        x.raw_product_name, x.name_source = nm["name"], nm["source"]
        if nm["name"].lower() not in (x.search_text or ""):
            x.search_text = f"{nm['name'].lower()} {x.search_text or ''}"[:4000]
    return survivor.id


# ------------------------------------------------------------------ AI pass
SCHEMA = {
    "type": "object",
    "properties": {"results": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "i": {"type": "integer"},
            "product_name": {"type": ["string", "null"]},
            "product_type": {"type": ["string", "null"]},
            "confidence": {"type": "number"},
            "evidence": {"type": "string"},
            "is_physical_product": {"type": "boolean"},
            "category": {"type": "string", "enum": ["beauty", "health", "home", "gadgets", "fashion", "pets", "kids", "food", "other", "service"]},
        },
        "required": ["i", "product_name", "product_type", "confidence", "evidence", "is_physical_product", "category"],
        "additionalProperties": False}}},
    "required": ["results"], "additionalProperties": False,
}
SYSTEM = (
    "You name the product each e-commerce ad sells. Ads are in any language (English, Tagalog/Taglish, Arabic, Vietnamese, Chinese, Spanish…). "
    "For every numbered item return: product_name = a short generic English product name (2–6 words, e.g. 'Magnetic Posture Corrector', "
    "'Blue Light Blocking Glasses'), product_type = the bare product noun ('posture corrector'), confidence 0–1, evidence = the exact words "
    "from the ad that show it (copied, original language). Rules: name the PRODUCT, never the shop, page, brand slogan, platform, price, "
    "offer or call to action ('Shop now', 'Order today', 'Free shipping'). If the ad does not say which product it sells, product_name=null "
    "and confidence 0 — never guess from the shop name or the picture you cannot see. is_physical_product=false for services, apps, courses, "
    "media, events, real estate. Use the number as i."
)


def _context(p: Product, ads: list[Ad], adv: dict) -> str:
    a = max(ads, key=lambda x: len(x.ad_text or x.title or "")) if ads else None
    if a is None:
        return ""
    md = (a.product_metadata or {}).get("title")
    ocr = next((c for c in (getattr(p, "_ocr", None) or []) if c), None)
    parts = [f"title={(a.title or '')[:120]!r}", f"text={(a.ad_text or '')[:360]!r}", f"url={(a.landing_url or '')[:120]!r}"]
    if md:
        parts.append(f"page_title={md[:120]!r}")
    if ocr:
        parts.append(f"image_text={ocr[:120]!r}")
    if adv.get(a.advertiser_id):
        parts.append(f"advertiser={adv[a.advertiser_id][:60]!r}")
    return " ".join(parts)


def batch_size(per_item_tokens: int) -> int:
    """10–30 items, fitted into the first ready provider's live token headroom (rate-limit headers)."""
    from .services import llm_providers as P

    for name in llm.chain():
        room = P.headroom(name) if name in ("groq", "cerebras", "cloudflare") else None
        if room is None:
            return 20
        fit = (room - 900) // max(1, per_item_tokens)
        return max(MIN_BATCH, min(MAX_BATCH, fit)) if fit >= MIN_BATCH else MIN_BATCH
    return MIN_BATCH


def _pending_ai(db, limit: int):
    return db.scalars(select(Product).where(
        Product.name_status == "UNKNOWN", Product.naming_checked_at.is_not(None),
        or_(Product.name_source.is_(None), Product.name_source.not_in(DONE_SOURCES)),
        func.coalesce(Product.naming_attempts, 0) < MAX_ATTEMPTS,
        or_(Product.manual_override.is_(None), Product.manual_override.is_(False)))
        .order_by(Product.in_target.desc().nullslast(), Product.opportunity_score.desc(), Product.id.desc()).limit(limit)).all()


def ai_pass(db, batches: int = 2, lock=None) -> dict:
    """Up to `batches` model requests of 10–30 products each. Stops at the first request no provider could answer (they are all
    rate limited: the next tick continues where this one stopped — nothing is marked done without an answer)."""
    from contextlib import nullcontext

    lock = lock or nullcontext
    out = {"requests": 0, "asked": 0, "named": 0, "review": 0, "no_product": 0, "failed": 0, "provider": None, "product_ids": []}
    adv = dict(db.execute(select(Advertiser.id, Advertiser.name)).all())
    for _ in range(batches):
        db.commit()  # nothing of ours stays open while the providers' shared state / ledger (own sessions) and the network run
        if not llm.available():
            out["skipped"] = "no provider ready (all rate limited / not configured)"
            break
        size = batch_size(per_item_tokens=_env_int("NAMING_TOKENS_PER_ITEM", 230))
        ps = _pending_ai(db, size)
        if not ps:
            break
        items = []
        for p in ps:
            ads = list(db.scalars(select(Ad).where(Ad.product_id == p.id, or_(Ad.ad_type.is_(None), Ad.ad_type.not_in(["CONTENT_AD"]))).limit(5)))
            p._ocr = list(db.scalars(select(Creative.ocr_text).where(Creative.product_id == p.id, Creative.ocr_text.is_not(None), Creative.ocr_text != "").limit(1)))
            items.append((p, ads, _context(p, ads, adv)))
        lines = "\n".join(f"{i}. {ctx}" for i, (_, _, ctx) in enumerate(items))
        db.commit()
        data = llm.complete_json(SYSTEM, lines, SCHEMA, max_tokens=min(8000, 220 * len(items) + 1200), timeout=60)
        out["requests"] += 1
        out["asked"] += len(items)
        if not data or not isinstance(data.get("results"), list):
            for p, _, _ in items:
                p.naming_attempts = (p.naming_attempts or 0) + 1
            out["failed"] += len(items)
            db.flush()
            break
        out["provider"] = llm.last_provider.get("name")
        by_i = {r.get("i"): r for r in data["results"] if isinstance(r, dict)}
        with lock():
            for i, (p, ads, _) in enumerate(items):
                if db.get(Product, p.id) is None or p.name_status != "UNKNOWN" or p.manual_override:
                    continue  # merged / named meanwhile, or a MKT name
                r = by_i.get(i)
                p.naming_attempts = (p.naming_attempts or 0) + 1
                if r is None:  # the model skipped this line: no answer is not "no product" — stays queued (≤ MAX_ATTEMPTS)
                    out["unanswered"] = out.get("unanswered", 0) + 1
                    continue
                verdict = judge(r, ads, adv)
                if verdict["status"] == "named":
                    nm = {"name": verdict["name"], "source": "llm", "confidence": verdict["confidence"], "status": "SUGGESTED" if verdict["confidence"] < 0.9 else "AUTO_CONFIRMED",
                          "evidence": [{"source": "llm", "value": verdict["name"], "words": verdict["evidence"][:120], "provider": out["provider"]}]}
                    out["product_ids"].append(_name(db, p, ads, nm))
                    out["named"] += 1
                elif verdict["status"] == "review":
                    p.name_source, p.name_confidence = "needs_review", verdict["confidence"]
                    N._add_evidence(p, [{"source": "llm_hint", "value": verdict["name"], "confidence": verdict["confidence"], "why": verdict["why"]}])
                    out["review"] += 1
                else:
                    p.name_source = "llm_none"
                    if r and not r.get("is_physical_product", True):
                        for x in ads:
                            x.ad_type = "SERVICE_AD"
                    out["no_product"] += 1
            db.flush()
    return out


def judge(r: dict | None, ads: list[Ad], adv: dict) -> dict:
    """Accept a model answer only when it is a product type, grounded in the ad's own words, not a shop / CTA, and confident."""
    if not r or not r.get("product_name"):
        return {"status": "none"}
    name = N._clean(str(r["product_name"]))[:120] if hasattr(N, "_clean") else str(r["product_name"]).strip()[:120]
    conf = max(0.0, min(1.0, float(r.get("confidence") or 0)))
    a = max(ads, key=lambda x: len(x.ad_text or x.title or "")) if ads else None
    advertiser = adv.get(a.advertiser_id) if a else None
    ad = {"title": a.title, "ad_text": a.ad_text, "landing_url": a.landing_url, "product_url": a.landing_url} if a else {}
    why = []
    if not r.get("is_physical_product", True):
        return {"status": "none"}
    if N.is_bad_name(name, advertiser) or N.is_bad_product_name(name, advertiser):
        why.append("shop_or_cta")
    if not N.grounded(name, ad) and not (r.get("evidence") and _evidence_in_ad(r["evidence"], ad)):
        why.append("not_grounded")
    if not (lexicon.has_product_noun(name) or lexicon.has_product_noun(r.get("product_type") or "")) and conf < 0.85:
        why.append("no_product_noun")
    if conf < ACCEPT_CONF:
        why.append("low_confidence")
    if why:
        return {"status": "review" if "shop_or_cta" not in why else "none", "name": name, "confidence": round(min(conf, 0.4), 2), "why": why}
    return {"status": "named", "name": name, "confidence": round(conf, 2), "evidence": str(r.get("evidence") or "")}


def _evidence_in_ad(evidence: str, ad: dict) -> bool:
    blob = " ".join(str(ad.get(k) or "") for k in ("title", "ad_text", "landing_url")).lower()
    words = [w for w in evidence.lower().split() if len(w) > 2]
    return bool(words) and sum(1 for w in words if w in blob) >= max(1, len(words) // 2)


# ------------------------------------------------------------------ dashboard numbers
def stats(db) -> dict:
    from .services import llm_providers as P

    q = lambda *c: db.scalar(select(func.count(Product.id)).where(*c)) or 0
    by_status = dict(db.execute(select(func.coalesce(Product.name_status, "LEGACY"), func.count()).group_by(Product.name_status)).all())
    by_source = dict(db.execute(select(func.coalesce(Product.name_source, "—"), func.count()).group_by(Product.name_source)).all())
    conf = dict(db.execute(select(func.coalesce(Product.name_status, "LEGACY"), func.avg(Product.name_confidence)).group_by(Product.name_status)).all())
    providers = {}
    for name in P.providers():
        st = (P._state_rows().get(name) or {}) if name in ("groq", "cerebras", "cloudflare") else {}
        ready, why = (P.ready(name) if name in ("groq", "cerebras", "cloudflare") else ((llm._gemini(), None) if name == "gemini" else (llm._claude(), None)))
        try:
            today = P.usage_today(name)
        except Exception:  # noqa: BLE001
            today = {}
        providers[name] = {"configured": (P.config(name) is not None) if name in ("groq", "cerebras", "cloudflare") else (llm._gemini() or bool(os.getenv("GEMINI_API_KEY"))) if name == "gemini" else llm._claude(),
                           "model": (P.config(name) or {}).get("model") if name in ("groq", "cerebras", "cloudflare") else (llm.GEMINI_MODEL if name == "gemini" else llm.CLAUDE_MODEL),
                           "ready": ready, "why": why, "limits": st.get("limits") or {}, "cooldown_until": st.get("cooldown_until"),
                           "last_error": st.get("last_error"), "today": today}
    return {
        "products": q(), "named": q(Product.name_status.in_(["VERIFIED", "AUTO_CONFIRMED", "SUGGESTED"])), "unknown": by_status.get("UNKNOWN", 0),
        "verified": by_status.get("VERIFIED", 0), "auto_confirmed": by_status.get("AUTO_CONFIRMED", 0), "suggested": by_status.get("SUGGESTED", 0),
        "needs_review": by_source.get("needs_review", 0), "no_product": by_source.get("llm_none", 0),
        "errors": q(Product.name_status == "UNKNOWN", func.coalesce(Product.naming_attempts, 0) >= MAX_ATTEMPTS),
        "queue_rules": q(Product.name_status == "UNKNOWN", Product.naming_checked_at.is_(None)),
        "queue_ai": q(Product.name_status == "UNKNOWN", Product.naming_checked_at.is_not(None),
                      or_(Product.name_source.is_(None), Product.name_source.not_in(DONE_SOURCES)), func.coalesce(Product.naming_attempts, 0) < MAX_ATTEMPTS),
        "by_source": by_source, "avg_confidence": {k: round(v, 2) if v is not None else None for k, v in conf.items()},
        "named_by_ai": by_source.get("llm", 0), "providers": providers, "engine": llm.engine(),
    }
