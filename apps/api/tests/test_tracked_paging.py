"""Tracked queries page deeper across runs: each run continues from the stored Meta cursor instead of re-reading the same
first `limit` ads (live 2026-10-26: 26 re-runs → 40 fetched, 0 new each); an expired cursor or an old one restarts at page 1."""
from datetime import datetime, timedelta
from types import SimpleNamespace

from app.collector.adapters.meta import MetaLibraryConnector
from app.collector.base import ConnectorError, FetchParams
from app.collector.contract import AdRecord
from app.realtime import _params_from


def fake_fetch(self, params, country, st=None):
    st = st or {}
    if st.get("cursor") == "expired":
        raise ConnectorError("Meta Ad Library: cursor refused")
    n = int(st.get("cursor") or 0)
    recs = [AdRecord(source="meta_library", source_ad_id=f"a{n * 2 + i}", platform="facebook", ad_text="x") for i in range(2)]
    return recs, {"cursor": str(n + 1), "sid": "s", "tok": "t", "done": n + 1 >= 5}


def ids(params):
    return [r.source_ad_id for r in MetaLibraryConnector({}).fetch_ads(params)]


def test_runs_continue_deeper_and_restart_on_a_refused_cursor(monkeypatch):
    monkeypatch.setattr(MetaLibraryConnector, "fetch_page", fake_fetch)
    first = FetchParams(query="free shipping", countries=["PH"], limit=4)
    assert ids(first) == ["a0", "a1", "a2", "a3"]
    nxt = FetchParams(query="free shipping", countries=["PH"], limit=4, extra={"resume": first.extra["resume_out"]})
    assert ids(nxt) == ["a4", "a5", "a6", "a7"]  # the next ads, not the same first page again
    stale = FetchParams(query="free shipping", countries=["PH"], limit=2, extra={"resume": {"PH": {"cursor": "expired"}}})
    assert ids(stale) == ["a0", "a1"]


def test_params_resume_only_from_a_fresh_unfinished_cursor():
    c = SimpleNamespace(config={}, adapter="meta_library")
    q = lambda at, done=False: SimpleNamespace(query="cod", countries=["PH"], page_ids=[], media_type=None, limit=40,
                                               cursor={"at": at.isoformat(), "pages": {"PH": {"cursor": "7", "done": done}}})
    now = datetime.utcnow()
    assert _params_from(c, q(now - timedelta(hours=1))).extra["resume"] == {"PH": {"cursor": "7", "done": False}}
    assert _params_from(c, q(now - timedelta(hours=30))).extra == {}         # too old: page 1 again (new ads surface there)
    assert _params_from(c, q(now - timedelta(hours=1), done=True)).extra == {}  # results ended: start over
