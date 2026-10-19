"""Search keyword → what product it means, whatever the language (Vietnamese, Tagalog, Arabic, Chinese, Spanish, Thai…).

Layers, cheapest first:
  1. lexicon.py   ~80 hand-made concepts × en / vi / tl / ar / zh — free, instant, exact. Used when it understands every word.
  2. query_intents cache   a keyword the model already explained: free, forever.
  3. one model call (Gemini / Claude via llm.py) for a keyword the lexicon cannot read — e.g. "kính mắt" (eyeglasses) or
     "organizador de maquillaje" — cached afterwards. Budget QUERY_LLM_DAILY calls / day (the free Gemini tier is 20, shared).
No answer (model off, budget spent) → the keyword is matched as written, never guessed.

expand() has the shape of lexicon.expand(), so keyword_tiers / name matching in routers/intel.py use either unchanged.
`variants` are ONE group (any of them = the product); `related` neighbours are shown below real matches, only when few.
ponytail: variants are not verified against the catalogue; a wrong one shows as an off-topic ad that the user can reject (/clusters
or Loại trừ). A reviewed-synonym table would be the upgrade.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import QueryIntent
from . import lexicon
from . import llm

log = logging.getLogger(__name__)
DAILY_CALLS = int(os.getenv("QUERY_LLM_DAILY", "8"))
_spent = {"day": None, "n": 0}
_mem: dict[str, dict | None] = {}   # process cache over the table (None = asked, not a product)
_failed: dict[str, float] = {}        # key → until when not to ask again (the model failed / was rate limited)
RETRY_AFTER_S = 600
LANG_OF_COUNTRY = {"PH": "tl", "VN": "vi", "CN": "zh", **dict.fromkeys(["SA", "AE", "KW", "QA", "OM", "BH", "JO", "EG", "IQ", "LB", "MA"], "ar")}

SCHEMA = {
    "type": "object",
    "properties": {
        "is_product": {"type": "boolean"},
        "product": {"type": "string"},
        "variants": {"type": "array", "items": {"type": "string"}},
        "related": {"type": "array", "items": {"type": "string"}},
        "search_terms": {"type": "object", "properties": {k: {"type": "string"} for k in ("en", "vi", "tl", "ar", "zh", "es", "th", "id")}},
    },
    "required": ["is_product", "product", "variants", "related", "search_terms"],
}
SYSTEM = (
    "You are the multilingual product-search brain of an e-commerce ad-spy tool. The user types a keyword in ANY language "
    "(Vietnamese, Tagalog/Filipino, Arabic, Chinese, Spanish, Thai, Indonesian, English…). Work out which physical product they "
    "mean. Beware words with two meanings — Vietnamese 'kính mắt' is eyeglasses (not window glass, not sunglasses). "
    "product: short generic English name. variants: 10-25 lowercase words / short phrases (≤ 3 words, no brands) that sellers and "
    "buyers use for EXACTLY this product, across en, vi, tl, ar, zh, es, th, id, pt, fr — never look-alike different products. "
    "related: up to 6 neighbouring products worth showing lower. search_terms: the single best ad-library search phrase per language. "
    "is_product=false (and empty lists) when it is a service, brand, person, app, gibberish or too vague to be one product."
)


def _budget() -> bool:
    today = date.today()
    if _spent["day"] != today:
        _spent.update(day=today, n=0)
    if _spent["n"] >= DAILY_CALLS or not llm.available():
        return False
    _spent["n"] += 1
    return True


def _clean(items, limit: int) -> list[str]:
    out = []
    for v in items or []:
        v = lexicon.fold(str(v))
        if len(v) >= 2 and len(v.split()) <= 4 and v not in out:
            out.append(v)
    return out[:limit]


def lookup(db: Session, query: str, ask: bool = True) -> dict | None:
    """The cached / freshly asked meaning of a keyword, or None (not a product, model off, budget spent)."""
    key = lexicon.fold(query)[:255]
    if key in _mem:
        return _mem[key]
    row = db.get(QueryIntent, key)
    if row is not None:
        _mem[key] = row.intent if row.source == "llm" and row.intent.get("variants") else None
        return _mem[key]
    import time

    if not ask or len(key) < 2 or len(key) > 80 or _failed.get(key, 0) > time.time() or not _budget():
        return None
    # a search request waits on this: one try, short timeout (a 429 used to hold /api/ads?q= for ~20 s while the client retried)
    got = llm.complete_json(SYSTEM, f"Keyword: {query}", SCHEMA, max_tokens=900, retries=1, timeout=8)
    if not got:   # transient failure (rate limit / timeout): not cached in the table, not asked again for RETRY_AFTER_S
        _failed[key] = time.time() + RETRY_AFTER_S
        return None
    it = {"product": lexicon.fold(got.get("product") or ""), "variants": _clean(got.get("variants"), 25) if got.get("is_product") else [],
          "related": _clean(got.get("related"), 6) if got.get("is_product") else [],
          "search_terms": {k: v.strip() for k, v in (got.get("search_terms") or {}).items() if isinstance(v, str) and v.strip()}}
    db.add(QueryIntent(query=key, intent=it, source="llm" if it["variants"] else "none"))
    db.flush()
    _mem[key] = it if it["variants"] else None
    return _mem[key]


def expand(db: Session, query: str, ask: bool = True) -> dict:
    """lexicon.expand's shape + "source" (lexicon | llm | none). The lexicon wins when it reads every word of the keyword."""
    e = lexicon.expand(query)
    if e["groups"] and not [w for w in e["rest"] if len(w) > 2]:
        return {**e, "source": "lexicon"}
    it = lookup(db, query, ask)
    if not it:
        return {**e, "source": "lexicon" if e["groups"] else "none"}
    variants = list(dict.fromkeys([lexicon.fold(query), it["product"], *it["variants"]]))
    variants = [v for v in variants if len(v) >= 2]
    return {"groups": [variants], "related": [list(dict.fromkeys([*variants, *it["related"]]))], "rest": [],
            "concepts": [it["product"]], "source": "llm", "intent": it}


def source_queries(db: Session, query: str, countries: list[str], n: int = 2) -> list[str]:
    """Extra keywords for a live crawl: the same product in English and in the selected markets' language, so a Vietnamese
    query still finds Philippine / Gulf ads. Lexicon concepts keep their own mapping (lexicon.english_queries)."""
    e = lexicon.expand(query)
    if e["groups"] and not [w for w in e["rest"] if len(w) > 2]:
        return lexicon.english_queries(query, n)
    it = lookup(db, query)
    if not it:
        return []
    terms = it.get("search_terms") or {}
    langs = ["en", *dict.fromkeys(LANG_OF_COUNTRY[c] for c in countries if c in LANG_OF_COUNTRY)]
    out = [terms[l] for l in langs if terms.get(l)]
    return [q for q in dict.fromkeys(out) if lexicon.fold(q) != lexicon.fold(query)][:n]
