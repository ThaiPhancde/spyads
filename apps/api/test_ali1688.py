"""Plain-assert self-check for the 1688 hybrid connector: python test_ali1688.py (run from apps/api). No network."""
import base64
import importlib.util
import sys
from pathlib import Path
from unittest import mock

from app.collector.adapters import ali1688
from app.collector.base import ConnectorError, FetchParams
from app.collector.contract import AdRecord

# 1. signature == the reference implementation (reference/1688-shopkeeper/scripts/_auth.py), same time + nonce
ref_dir = Path(__file__).resolve().parents[2] / "reference" / "1688-shopkeeper" / "scripts"
if ref_dir.exists():
    sys.path.insert(0, str(ref_dir))
    spec = importlib.util.spec_from_file_location("_ref_auth", ref_dir / "_auth.py")
    ref = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ref)
    body = '{"query": "lucky bracelet", "channel": ""}'
    with mock.patch.object(ref.time, "time", return_value=1700000000), \
         mock.patch.object(ref.uuid, "uuid4", return_value=mock.Mock(hex="abcdef1234567890")):
        want = ref.build_signature("POST", ali1688.SEARCH_PATH, body, "application/json", "AKID", "S" * 32)
    got = ali1688.sign(ali1688.SEARCH_PATH, body, "AKID", "S" * 32, ts="1700000000", nonce="abcdef12")
    assert got == want, (got, want)
    sys.path.remove(str(ref_dir))

# 2. AK split: plain and base64 forms
assert ali1688.split_ak("S" * 32 + "myid") == ("myid", "S" * 32)
assert ali1688.split_ak(base64.urlsafe_b64encode(("S" * 32 + "myid").encode()).decode()) == ("myid", "S" * 32)
assert ali1688.split_ak("short") is None

# 3. parsing: real fields kept, missing ones None (no invented data)
model = {"data": {
    "991122553819": {"title": "幸运手链 女", "price": "3.5", "image": "//cbu01.alicdn.com/a.jpg",
                     "stats": {"last30DaysSales": 1268, "remarkCnt": 32, "goodRates": 0.96}},
    "894138137003": {"title": "转运珠手链"},
}}
recs = ali1688.parse_offers(model, "lucky bracelet", 20)
assert [r.source_ad_id for r in recs] == ["991122553819", "894138137003"]
a, b = recs
assert a.platform == "1688" and a.price == 3.5 and a.currency == "CNY" and a.sold_count == 1268 and a.review_count == 32
assert a.media[0].url == "https://cbu01.alicdn.com/a.jpg" and a.product_url == "https://detail.1688.com/offer/991122553819.html"
assert b.price is None and b.sold_count is None and b.media == [] and b.raw_source["stats"] is None
assert len(ali1688.parse_offers(model, "x", 1)) == 1
try:
    ali1688.parse_offers({"data": []}, "x", 5)
    raise AssertionError("bad shape must raise")
except ConnectorError:
    pass


# 4. router: enough AK results → no Apify; short / failing AK → Apify only if selected; Apify duplicates dropped
def apify_rec(oid):
    return AdRecord(source="apify_1688", source_ad_id=oid, platform="1688")


def run(cfg, ak_result, apify_ids=("991122553819", "555")):
    c = ali1688.Ali1688Connector(cfg)
    search = mock.Mock(side_effect=ak_result) if isinstance(ak_result, Exception) else mock.Mock(return_value=ak_result)
    with mock.patch.object(c, "search", search), \
         mock.patch.object(ali1688.Apify1688Connector, "fetch_ads", return_value=iter(map(apify_rec, apify_ids))) as fb:
        out = list(c.fetch_ads(FetchParams(query="lucky bracelet", limit=20)))
    return [(r.source, r.source_ad_id) for r in out], fb.called


many = [apify_rec(str(i)) for i in range(6)]  # 6 distinct ≥ min_results 5
out, paid = run({"apify_fallback": {}}, many)
assert not paid and len(out) == 6
out, paid = run({}, recs)  # short, Apify not selected → keep what AK gave, never spend
assert not paid and len(out) == 2
out, paid = run({"apify_fallback": {}}, recs)  # short + Apify selected → merge, offer 991122553819 not duplicated
assert paid and out == [("ali1688", "991122553819"), ("ali1688", "894138137003"), ("apify_1688", "555")], out
out, paid = run({"apify_fallback": {}}, ConnectorError("401"))  # AK blocked → Apify
assert paid and len(out) == 2
try:
    run({}, ConnectorError("Thiếu ALI_1688_AK"))
    raise AssertionError("AK failure without fallback must surface")
except ConnectorError as e:
    assert "Apify" in str(e)

# 5. Apify fallback failing (budget, token…) keeps the AK listings; with nothing at all it surfaces both reasons
c = ali1688.Ali1688Connector({"apify_fallback": {}})
with mock.patch.object(c, "search", return_value=recs), \
     mock.patch.object(ali1688.Apify1688Connector, "fetch_ads", side_effect=ConnectorError("budget")):
    assert [r.source_ad_id for r in c.fetch_ads(FetchParams(query="x", limit=20))] == ["991122553819", "894138137003"]
with mock.patch.object(c, "search", return_value=[]), \
     mock.patch.object(ali1688.Apify1688Connector, "fetch_ads", side_effect=ConnectorError("budget")):
    try:
        list(c.fetch_ads(FetchParams(query="x", limit=20)))
        raise AssertionError("both sources empty must raise")
    except ConnectorError as e:
        assert "budget" in str(e)

# 6. retries re-sign (fresh nonce per attempt); 401 is not retried
c = ali1688.Ali1688Connector({"ak": "S" * 32 + "myid"})
ok = mock.Mock(json=lambda: {"success": True, "model": model})
with mock.patch.object(c, "request", side_effect=[ConnectorError("HTTP 503"), ok]) as req, mock.patch.object(ali1688.time, "sleep"):
    assert len(c.search("x", 20)) == 2
    nonces = [call.kwargs["headers"]["x-csk-nonce"] for call in req.call_args_list]
    assert len(nonces) == 2 and nonces[0] != nonces[1]
from app.collector.base import AuthExpired
with mock.patch.object(c, "request", side_effect=AuthExpired("401")) as req:
    try:
        c.search("x", 20)
        raise AssertionError
    except AuthExpired:
        assert req.call_count == 1

# 7. banded string stats from the live API
assert [ali1688._count(v) for v in (1268, "20+", "300+", "<10", None, "")] == [1268, 20, 300, None, None, None]

# 8. more than 20 wanted → one query per 20 with a different variant, de-duplicated, ranked, capped at limit
c = ali1688.Ali1688Connector({})
with mock.patch.object(c, "search", side_effect=lambda q, n: [apify_rec(str(len(q) * 100 + k)) for k in range(20)]) as sr:
    out = list(c.fetch_ads(FetchParams(query="x", limit=45)))
assert [call.args[0] for call in sr.call_args_list] == ["x", "x 爆款", "x 低价批发"], sr.call_args_list
assert len(out) == 45 and len({r.source_ad_id for r in out}) == 45 and [r.rank for r in out] == list(range(1, 46))

print("ok")
