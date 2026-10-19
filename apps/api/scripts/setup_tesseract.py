"""Prepare Tesseract for creative_enrich OCR: fetch the language packs it needs into data/tessdata (no admin rights, outside
Program Files). Run once:  .venv/Scripts/python scripts/setup_tesseract.py  [lang ...]   (default: eng fil ara vie)
Packs are tessdata_fast (small, ~1-4 MB each). The Tesseract program itself: `winget install UB-Mannheim.TesseractOCR`."""
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import creative_enrich as E  # noqa: E402

URL = "https://github.com/tesseract-ocr/tessdata_fast/raw/main/{}.traineddata"


def main(langs: list[str]) -> int:
    exe = E.tesseract_exe()
    if not exe:
        print("Tesseract not found — winget install UB-Mannheim.TesseractOCR (or set TESSERACT_CMD)")
        return 1
    E.TESSDATA.mkdir(parents=True, exist_ok=True)
    for lang in langs:
        dest = E.TESSDATA / f"{lang}.traineddata"
        if dest.exists() and dest.stat().st_size > 100_000:
            print(f"{lang}: already there")
            continue
        r = httpx.get(URL.format(lang), follow_redirects=True, timeout=120)
        r.raise_for_status()
        dest.write_bytes(r.content)
        print(f"{lang}: {len(r.content) // 1024} KB")
    print("installed packs:", sorted(E.tesseract_langs() or []))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["eng", "fil", "ara", "vie"]))
