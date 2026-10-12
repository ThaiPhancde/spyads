"""Offline check of the Grok Bot Allegro flow: config gate, webhook trigger (captured, not sent), callback auth, validation,
dedupe, cache, in-flight reuse, timeout, captcha/blocked. Throw-away SQLite. Run: .venv/Scripts/python test_allegro_bot.py"""
import os
import tempfile
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlparse

_tmp = tempfile.mkdtemp()
os.environ.update(DATABASE_URL=f"sqlite:///{_tmp}/bot.db", DISABLE_SCHEDULER="1", ADMIN_TOKEN="adm")
for k in ("GROK_ROUTINE_WEBHOOK_URL", "GROK_ROUTINE_WEBHOOK_KEY", "ALLEGRO_CALLBACK_SECRET", "PUBLIC_API_BASE_URL", "GROK_TOOL_TOKEN"):
    os.environ.pop(k, None)

from fastapi.testclient import TestClient  # noqa: E402

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.eu_market import allegro_bot as ab  # noqa: E402
from app.eu_market.models import EUBotJob, EUListing  # noqa: E402
from app.main import app  # noqa: E402

Base.metadata.create_all(engine)
c, H = TestClient(app), {"X-Admin-Token": "adm"}

# not configured -> clear 503, no job
r = c.post("/api/eu-market/allegro/search", json={"keyword": "zegarek", "provider": "grok"}, headers=H)
assert r.status_code == 503 and r.json()["detail"]["code"] == "missing_credentials", r.text
os.environ.update(GROK_ROUTINE_WEBHOOK_URL="https://bot.example/hook", GROK_ROUTINE_WEBHOOK_KEY="k", ALLEGRO_CALLBACK_SECRET="s3cret", GROK_TOOL_TOKEN="tt",
                  PUBLIC_API_BASE_URL="http://localhost:8000")
r = c.post("/api/eu-market/allegro/search", json={"keyword": "zegarek", "provider": "grok"}, headers=H)
assert r.json()["detail"]["code"] == "callback_not_public", r.text  # cloud bot can't reach localhost
os.environ["PUBLIC_API_BASE_URL"] = "https://spy.example.com"

sent = []


class Resp:
    status_code, text = 200, "ok"


def fake_post(url, json, timeout, headers):
    sent.append((url, json, headers))
    return Resp()


with SessionLocal() as db:
    j = ab.request_search(db, "  Bransoletka   damska ", 5, provider="grok", post=fake_post)
assert j["status"] == "sent" and len(sent) == 1
url, payload, hdr = sent[0]
assert hdr["Authorization"] == "Bearer k" and payload["task"]["keyword"] == "Bransoletka damska" and "s3cret" not in str(payload)
cb = urlparse(payload["callback"]["url"])
assert cb.netloc == "spy.example.com" and cb.path == f"/api/eu-market/allegro/callback/{j['id']}"
token = parse_qs(cb.query)["token"][0]
with SessionLocal() as db:  # same keyword in flight -> reused, no 2nd webhook
    assert ab.request_search(db, "bransoletka DAMSKA", 5, provider="grok", post=fake_post)["reused"] == "in_flight" and len(sent) == 1

cb_url = f"/api/eu-market/allegro/callback/{j['id']}"
assert c.post(cb_url, params={"token": token}, json={"status": "ok", "items": []}, headers={"X-Callback-Secret": "bad"}).status_code == 401
assert c.post(cb_url, params={"token": "x"}, json={"items": []}, headers={"X-Callback-Secret": "s3cret"}).status_code == 401
items = [{"offer_id": "17000000001", "title": "Bransoletka srebrna", "price": "59,99 zł", "seller": "sklep1",
          "url": "https://allegro.pl/oferta/bransoletka-17000000001", "image": "https://a.allegroimg.com/1.jpg"},
         {"offer_id": "17000000001", "title": "dup", "price": 1},  # same offer twice
         {"offer_id": "17000000002", "title": "Bransoletka złota", "price": 120, "url": "https://evil.example/x"},
         {"offer_id": "abc", "title": "not an offer id"}, {"title": "no id"}]
r = c.post(cb_url, params={"token": token}, json={"status": "ok", "items": items}, headers={"X-Callback-Secret": "s3cret"})  # no admin token needed
assert r.status_code == 200 and r.json()["status"] == "done" and r.json()["items_written"] == 2, r.text
with SessionLocal() as db:
    rows = db.query(EUListing).filter(EUListing.source == "allegro").all()
    assert len(rows) == 2 and {x.data_access for x in rows} == {"browser_agent"}
    assert {x.url for x in rows} == {"https://allegro.pl/oferta/bransoletka-17000000001", "https://allegro.pl/oferta/17000000002"}
# a retried callback is acknowledged, not re-applied
assert c.post(cb_url, params={"token": token}, json={"status": "ok", "items": items}, headers={"X-Callback-Secret": "s3cret"}).status_code == 200
g = c.get(f"/api/eu-market/allegro/jobs/{j['id']}").json()
assert g["status"] == "done" and len(g["rows"]) == 2 and g["rows"][0]["offers"][0]["currency"] == "PLN"

with SessionLocal() as db:  # cache hit within ALLEGRO_BOT_CACHE_HOURS, force -> new run
    assert ab.request_search(db, "bransoletka damska", 5, provider="grok", post=fake_post)["reused"] == "cache" and len(sent) == 1
    j2 = ab.request_search(db, "bransoletka damska", 5, force=True, provider="grok", post=fake_post)
    assert len(sent) == 2 and j2["status"] == "sent"
tok2 = parse_qs(urlparse(sent[1][1]["callback"]["url"]).query)["token"][0]
r = c.post(f"/api/eu-market/allegro/callback/{j2['id']}", params={"token": tok2}, json={"status": "captcha", "secret": "s3cret"})
assert r.json()["status"] == "captcha"
assert "CAPTCHA" in c.get(f"/api/eu-market/allegro/jobs/{j2['id']}").json()["message"]

with SessionLocal() as db:  # webhook down -> failed; deadline passed -> timeout
    bad = ab.request_search(db, "lampa", 5, provider="grok", post=lambda *a, **k: (_ for _ in ()).throw(ConnectionError("down")))
    assert bad["status"] == "failed" and bad["error_code"] == "webhook_failed"
    j3 = ab.request_search(db, "kubek", 5, provider="grok", post=fake_post)
    db.get(EUBotJob, j3["id"]).deadline_at = datetime.utcnow() - timedelta(seconds=1)
    db.commit()
assert c.get(f"/api/eu-market/allegro/jobs/{j3['id']}").json()["status"] == "timeout"
st = c.get("/api/eu-market/allegro/bot").json()
assert st["config"]["public_base_ok"] and st["http_crawler_enabled"] is False and "k" not in str(st["config"].values())
print("allegro bot OK")
