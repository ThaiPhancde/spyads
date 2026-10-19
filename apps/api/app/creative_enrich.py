"""Selective OCR / transcript for the Wave / Creative signals (worker scheduler, or `python -m app.creative_enrich [--limit N] [--dry-run]`).

Only where the extra text can change a decision, and cheapest source first:
  1. metadata the ad already has (caption, headline, and OCR / transcript already on its creatives) — services/creative_text.py;
  2. OCR of the creative's stored thumbnail (≤480 px JPEG, never the video): local Tesseract when installed (CPU only), else one
     Gemini vision call;
  3. transcript — video only, only when the ad is still unreadable after OCR: the already-stored file when ≤ MAX_CLIP, else the
     source's light (SD) rendition downloaded with a hard byte cap, sent as-is to Gemini. Nothing is re-encoded, nothing is
     downloaded in full.
The thumbnail outlives the video (most creatives are 'expired' but keep thumb_key), so OCR works on those too.
Scope: ads of TEST_NOW / TEST products whose copy is too thin (reliability < 0.5) — everything else keeps the metadata signals.
`ocr_text` / `transcript` = '' means "tried, nothing there" (never retried); NULL = not tried (engine missing / budget spent).
Gemini free tier is 20 calls/day shared with ingest + naming: CREATIVE_LLM_DAILY (default 6) is this job's share.
ponytail: in-process day counter (a worker restart re-grants it); one OCR language set per ad, no per-line language switching.
"""
from __future__ import annotations

import argparse
import io
import logging
import os
import re
import shutil
from datetime import date

import httpx
from sqlalchemy import or_, select

from .models import Ad, Creative, Product
from .services import llm
from .services.creative_text import ad_text_evidence, needs_text
from .services.enrichment import classify_creative
from .storage import DATA_DIR, store

log = logging.getLogger(__name__)
DAILY_CALLS = int(os.getenv("CREATIVE_LLM_DAILY", "6"))
MAX_CLIP = int(os.getenv("CREATIVE_CLIP_MAX_MB", "6")) * 1024 * 1024  # biggest clip we will send for a transcript
OCR_PER_TICK = int(os.getenv("CREATIVE_OCR_PER_TICK", "10"))
TRANSCRIPTS_PER_TICK = int(os.getenv("CREATIVE_TRANSCRIPTS_PER_TICK", "2"))
ADS_PER_PRODUCT = 2
_spent = {"day": None, "n": 0}
TESSDATA = DATA_DIR / "tessdata"
_TL_COUNTRIES, _VI_COUNTRIES, _AR_COUNTRIES = {"PH"}, {"VN"}, {"SA", "AE", "KW", "QA", "OM", "BH", "JO", "EG", "IQ", "LB", "MA", "DZ", "TN", "LY"}
_NONE = re.compile(r"^\W*none\W*$", re.I)


def _llm_budget(peek: bool = False) -> bool:
    """True when a Gemini call is allowed today; consumes one unless `peek` (used before a download we'd waste)."""
    today = date.today()
    if _spent["day"] != today:
        _spent.update(day=today, n=0)
    if _spent["n"] >= DAILY_CALLS or not llm.available():
        return False
    _spent["n"] += not peek
    return True


# ------------------------------------------------------------ OCR
def tesseract_exe() -> str | None:
    """TESSERACT_CMD, PATH, or the UB-Mannheim default install (the installer does not add itself to PATH)."""
    for c in (os.getenv("TESSERACT_CMD"), shutil.which("tesseract"), r"C:\Program Files\Tesseract-OCR\tesseract.exe",
              r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"):
        if c and os.path.isfile(c):
            return c
    return None


def _use_tessdata() -> None:
    """Point Tesseract at our own tessdata dir (scripts/setup_tesseract.py puts fil / ara / vie there) when it has packs, else
    leave its default. An env var, not --tessdata-dir: pytesseract cannot pass a quoted path with spaces on Windows."""
    if any(TESSDATA.glob("*.traineddata")):
        os.environ["TESSDATA_PREFIX"] = str(TESSDATA)


def tesseract_langs() -> set[str] | None:
    """Installed language packs; None when the program is missing (OCR then falls back to Gemini)."""
    exe = tesseract_exe()
    if not exe:
        return None
    try:
        import pytesseract

        pytesseract.pytesseract.tesseract_cmd = exe
        _use_tessdata()
        return set(pytesseract.get_languages()) - {"osd"}
    except Exception as e:
        log.warning("tesseract unusable: %s", e)
        return None


def _lang_hint(ad: Ad) -> list[str]:
    """Tesseract packs worth loading for this ad: always English; Filipino ("fil") for PH; Arabic for the GCC / MENA markets; Vietnamese for VN."""
    c, lang = (ad.country or "").upper()[:2], (ad.language or "").lower()
    out = ["eng"]
    if c in _TL_COUNTRIES or lang[:2] in ("tl", "fi"):
        out.append("fil")
    if c in _AR_COUNTRIES or lang[:2] == "ar":
        out.append("ara")
    if c in _VI_COUNTRIES or lang[:2] == "vi":
        out.append("vie")
    return out


def _clean_ocr(words: list[str]) -> str:
    return re.sub(r"\s+", " ", " ".join(words)).strip()[:600]


def ocr_image(data: bytes, ad: Ad) -> tuple[str | None, str]:
    """(text, engine). text None = no engine could run (nothing is recorded, a later tick retries); '' = ran, nothing legible."""
    have = tesseract_langs()
    if have is not None:
        try:
            import pytesseract
            from PIL import Image

            langs = [l for l in _lang_hint(ad) if l in have] or ["eng"]
            img = Image.open(io.BytesIO(data)).convert("L")
            if img.width < 900:  # thumbnails are ≤ 480 px wide: Tesseract wants ~30 px glyphs
                img = img.resize((img.width * 2, img.height * 2))
            d = pytesseract.image_to_data(img, lang="+".join(langs), config="--psm 11", output_type=pytesseract.Output.DICT)
            # only confident words: busy product photos make Tesseract invent short noise tokens
            words = [w for w, c in zip(d["text"], d["conf"]) if w.strip() and float(c) >= 60 and sum(ch.isalpha() for ch in w) >= 2]
            return _clean_ocr(words), "tesseract"
        except Exception as e:
            log.warning("tesseract failed: %s", e)
    if _llm_budget():
        out = llm.media_text("Read every piece of text visible in this ad image (captions, prices, slogans) in its original language "
                             "(Tagalog, Arabic, English, ...). Return only the text, no commentary. If there is no text return NONE.",
                             data, "image/jpeg", 500)
        if out is not None:
            return ("" if _NONE.match(out) else _clean_ocr(out.split())), "gemini"
    return None, "none"


# ------------------------------------------------------------ transcript
def _clip(c: Creative) -> tuple[bytes, str] | None:
    """The video bytes to transcribe without re-encoding: the stored file if small enough, else the light rendition under a
    byte cap (a truncated mp4 is useless, so over the cap = give up)."""
    if c.storage_key and c.status == "stored" and 0 < (c.size_bytes or 0) <= MAX_CLIP:
        try:
            return store().get(c.storage_key), c.mime or "video/mp4"
        except Exception:
            pass
    url = c.sd_source_url
    if not url:
        return None
    try:
        buf = bytearray()
        with httpx.stream("GET", url, timeout=30, follow_redirects=True) as r:
            if r.status_code in (403, 404, 410):
                return b"", ""  # signed CDN link expired: never coming back
            if r.status_code != 200:
                return None
            if int(r.headers.get("content-length") or 0) > MAX_CLIP:
                return b"", ""  # too big: decided, not an error
            for chunk in r.iter_bytes(65536):
                buf += chunk
                if len(buf) > MAX_CLIP:
                    return b"", ""
        return bytes(buf), (r.headers.get("content-type") or "video/mp4").split(";")[0]
    except httpx.HTTPError:
        return None


def transcribe(c: Creative) -> str | None:
    """'' = no speech / too big to afford; None = could not run (retry later)."""
    if not _llm_budget(peek=True):  # don't fetch a clip we cannot afford to send
        return None
    clip = _clip(c)
    if clip is None:
        return None
    if not clip[0]:
        return ""
    if not _llm_budget():
        return None
    out = llm.media_text("Transcribe all speech and any burned-in subtitles in this ad video, in the language spoken (Tagalog, Arabic, "
                         "English, ...). Return only the transcript. If there is no speech and no subtitles return NONE.",
                         clip[0], clip[1], 1200)
    if out is None:
        return None
    return "" if _NONE.match(out) else re.sub(r"\s+", " ", out).strip()[:1500]


# ------------------------------------------------------------ the job
def _evidence(ad: Ad, creatives: list[Creative]) -> dict:
    return ad_text_evidence(ad, " ".join(c.ocr_text or "" for c in creatives), " ".join(c.transcript or "" for c in creatives))


def candidates(db, limit: int = 30) -> list[tuple[Product, Ad, list[Creative]]]:
    """(product, ad, its creatives) for unreadable ads of TEST_NOW first, then TEST — strongest products first."""
    prods = db.scalars(select(Product).where(Product.recommendation.in_(["TEST_NOW", "TEST"]))
                       .order_by((Product.recommendation == "TEST_NOW").desc(), Product.opportunity_score.desc()).limit(limit)).all()
    out = []
    for p in prods:
        ads = db.scalars(select(Ad).where(Ad.product_id == p.id, Ad.is_active.is_(True), Ad.is_internal.is_(False))
                         .order_by(Ad.variants.desc().nullslast(), Ad.first_seen_at)).all()
        todo = 0
        for a in ads:
            cs = db.scalars(select(Creative).where(Creative.ad_id == a.id, or_(Creative.status == "stored", Creative.thumb_key.is_not(None))).order_by(Creative.position)).all()
            if cs and needs_text(_evidence(a, cs)) and any(c.ocr_text is None or (c.type == "video" and c.transcript is None) for c in cs):
                out.append((p, a, cs))
                todo += 1
                if todo >= ADS_PER_PRODUCT:
                    break
    return out


def _reclassify(a: Ad, text: str) -> None:
    """hook / angle / offer were read from the caption only: fill the ones that fell back to the classifier defaults."""
    cls = classify_creative(text)
    if a.hook in (None, "statement") and cls["hook"] != "statement":
        a.hook = cls["hook"]
    if a.angle in (None, "general") and cls["angle"] != "general":
        a.angle = cls["angle"]
    if not a.offer and cls["offer"]:
        a.offer = cls["offer"]


def tick(db, limit: int = 30, dry_run: bool = False) -> dict:
    cands = candidates(db, limit)
    out = {"candidates": len(cands), "ocr": 0, "transcripts": 0, "engine": None, "product_ids": [], "skipped": None}
    if dry_run:
        return out
    ocr_left, tr_left, touched, transcribed = OCR_PER_TICK, TRANSCRIPTS_PER_TICK, set(), set()
    for p, a, cs in cands:
        changed = False
        for c in cs:
            if ocr_left <= 0 or c.ocr_text is not None or not c.thumb_key:
                continue
            try:
                data = store().get(c.thumb_key)
            except Exception:
                continue
            text, eng = ocr_image(data, a)
            if text is None:  # no engine / budget: stop OCR for this tick, say why
                out["skipped"] = "ocr engine unavailable (install Tesseract or set GEMINI_API_KEY)"
                ocr_left = 0
                continue
            c.ocr_text, out["engine"], changed = text, eng, True
            out["ocr"] += 1
            ocr_left -= 1
            break  # one creative per ad: the cover is enough to read the caption overlay
        if changed:
            db.commit()
        # still unreadable after OCR → the audio/subtitles, video only, once per product
        if tr_left > 0 and p.id not in transcribed and needs_text(_evidence(a, cs)):
            vid = next((c for c in cs if c.type == "video" and c.transcript is None), None)
            if vid is not None:
                t = transcribe(vid)
                if t is not None:
                    vid.transcript, changed = t, True
                    out["transcripts"] += 1
                    tr_left -= 1
                    transcribed.add(p.id)
        if changed:
            ev = _evidence(a, cs)
            if ev["text"]:
                _reclassify(a, ev["text"])
            touched.add(p.id)
            db.commit()
    out["product_ids"] = sorted(touched)
    if touched:
        from .services.engine import rescore_products

        rescore_products(db, touched)  # wave / creative parts + confidence now see the new text
        db.commit()
    return out


if __name__ == "__main__":
    from .db import SessionLocal, auto_migrate

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--limit", type=int, default=30, help="products to look at (TEST_NOW first)")
    ap.add_argument("--dry-run", action="store_true", help="count the unreadable ads, call nothing")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO)
    auto_migrate()
    with SessionLocal() as s:
        print({k: v for k, v in tick(s, args.limit, args.dry_run).items() if k != "product_ids"})
