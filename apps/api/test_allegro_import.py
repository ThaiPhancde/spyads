"""Offline check for the Allegro file import (throw-away SQLite DB, fixture rows written by the test itself).
Run: cd apps/api && .venv/Scripts/python test_allegro_import.py"""
import json
import os
import tempfile
from pathlib import Path

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/allegro_test.db"
os.environ["DISABLE_SCHEDULER"] = "1"

from sqlalchemy import func, select  # noqa: E402

from app import models  # noqa: E402,F401 — registers tables
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.eu_market import allegro_import as ai  # noqa: E402
from app.eu_market.models import EUListing, EUPriceSnapshot  # noqa: E402

Base.metadata.create_all(engine)
missing = Path(_tmp) / "nope.json"
assert ai.status(missing)["state"] == "no_data"  # no file -> "no data", nothing invented
with SessionLocal() as db:
    try:
        ai.import_file(db, missing)
        raise AssertionError("must raise")
    except FileNotFoundError:
        pass

assert ai._num("1 299,00 zł") == 1299.0 and ai._num("1.299,50") == 1299.5 and ai._num({"amount": "12.99"}) == 12.99
assert ai._num(0) is None and ai._num("n/a") is None and ai._num(None) is None
assert ai.map_item({"title": "x"}) is None and ai.map_item({"offer_id": 1}) is None  # no id / no title -> skipped

rows = {"offers": [
    {"offer_id": "111", "title": "Bransoletka feng shui", "price": "49,99 zł", "seller": {"id": "9", "login": "sklep_a"},
     "image": "https://a.allegroimg.com/x.jpg", "url": "https://allegro.pl/oferta/111"},
    {"offer_id": "222", "title": "Zegarek męski", "price": {"amount": 120, "currency": "PLN"}, "seller": "sklep_b", "images": ["javascript:alert(1)"]},
    {"offer_id": "111", "title": "Bransoletka feng shui", "price": 44.99, "seller": "sklep_a", "image": "https://a.allegroimg.com/x.jpg", "url": "https://allegro.pl/oferta/111"},  # same offer twice: last wins
    {"title": "bez id"},
]}
f = Path(_tmp) / "allegro.json"
f.write_text(json.dumps(rows), encoding="utf-8")
assert ai.status(f)["rows"] == 4
with SessionLocal() as db:
    r = ai.import_file(db, f)
    assert (r["rows"], r["valid"], r["skipped_invalid"], r["duplicates_in_file"], r["written"]) == (4, 2, 1, 1, 2), r
    r2 = ai.import_file(db, f)  # idempotent: no new rows
    assert db.scalar(select(func.count()).select_from(EUListing)) == 2
    a = db.scalar(select(EUListing).where(EUListing.source_product_id == "111"))
    assert (a.source, a.market_country, a.currency, a.price, a.kind, a.seller_name) == ("allegro", "PL", "PLN", 44.99, "marketplace_listing", "sklep_a")
    assert a.thumbnail_url == "https://a.allegroimg.com/x.jpg" and a.url == "https://allegro.pl/oferta/111"
    b = db.scalar(select(EUListing).where(EUListing.source_product_id == "222"))
    assert b.thumbnail_url is None and b.url == "https://allegro.pl/oferta/222"  # non-http image rejected
    assert db.scalar(select(func.count()).select_from(EUPriceSnapshot)) == 2
print("allegro import OK")
