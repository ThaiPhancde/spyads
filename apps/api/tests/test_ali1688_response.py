import json
from unittest.mock import Mock

import pytest

from app.collector.adapters import ali1688
from app.collector.base import AuthExpired, ConnectorError, FetchParams


@pytest.mark.parametrize("data", [{}, [], "{}", "[]", "null", None])
def test_successful_empty_responses_are_not_schema_errors(data):
    assert ali1688.parse_offers({"success": True, "data": data}, "x", 20) == []


def test_object_list_and_json_encoded_payloads_have_same_offer_identity():
    row = {"offerId": "991122553819", "title": "Lucky bracelet", "price": "3.5", "image": "//cdn.test/a.jpg"}
    for data in ({"991122553819": row}, [row], json.dumps([row])):
        record = ali1688.parse_offers({"success": True, "data": data}, "x", 20)[0]
        assert record.source_ad_id == "991122553819" and record.currency == "CNY" and record.price == 3.5


@pytest.mark.parametrize("model", [{}, None, {"data": 42}, {"data": "not json"}, {"data": ["bad"]}, {"data": [{"title": "No ID"}]}, {"data": {"count": 3}}])
def test_invalid_responses_are_not_silent_empty_results(model):
    with pytest.raises(ConnectorError):
        ali1688.parse_offers(model, "x", 20)


def test_nested_business_failure_is_reported_as_business_error():
    with pytest.raises(ConnectorError, match="quota"):
        ali1688.parse_offers({"success": False, "message": "quota"}, "x", 20)


@pytest.mark.parametrize("bad", [{"success": True, "model": {}}, {"success": True, "model": {"data": 42}}, {"success": False, "msgCode": "500", "msgInfo": "temporary"}])
def test_search_retries_incomplete_responses_with_fresh_nonce(monkeypatch, bad):
    c = ali1688.Ali1688Connector({"ak": "s" * 32 + "test-id"})
    request = Mock(side_effect=[Mock(json=Mock(return_value=bad)), Mock(json=Mock(return_value={"success": True, "model": {"data": {"123": {"title": "Bracelet", "price": "5"}}}}))])
    monkeypatch.setattr(c, "request", request)
    monkeypatch.setattr(ali1688.time, "sleep", lambda *a: None)
    assert c.search("bracelet", 20)[0].source_ad_id == "123"
    assert request.call_count == 2
    headers = [call.kwargs["headers"] for call in request.call_args_list]
    assert headers[0]["x-csk-nonce"] != headers[1]["x-csk-nonce"]
    assert headers[0]["x-csk-sign"] != headers[1]["x-csk-sign"]


def test_persistently_missing_data_fails_after_three_attempts(monkeypatch):
    c = ali1688.Ali1688Connector({"ak": "s" * 32 + "test-id"})
    request = Mock(return_value=Mock(json=Mock(return_value={"success": True, "model": {}})))
    monkeypatch.setattr(c, "request", request)
    monkeypatch.setattr(ali1688.time, "sleep", lambda *a: None)
    with pytest.raises(ConnectorError, match="model.data"):
        list(c.fetch_ads(FetchParams(query="bracelet", limit=20)))
    assert request.call_count == 3


@pytest.mark.parametrize("payload,error", [({"success": False, "msgCode": "401", "msgInfo": "expired"}, AuthExpired), ({"success": True, "model": {"success": False, "message": "quota"}}, ConnectorError)])
def test_auth_and_business_failure_are_not_retried(monkeypatch, payload, error):
    c = ali1688.Ali1688Connector({"ak": "s" * 32 + "test-id"})
    request = Mock(return_value=Mock(json=Mock(return_value=payload)))
    monkeypatch.setattr(c, "request", request)
    with pytest.raises(error):
        c.search("bracelet", 20)
    request.assert_called_once()


def test_empty_later_variant_keeps_successful_results_without_paid_fallback(monkeypatch):
    c = ali1688.Ali1688Connector({})
    records = ali1688.parse_offers({"data": {str(100+i): {"title": "Bracelet"} for i in range(20)}}, "x", 20)
    monkeypatch.setattr(c, "search", Mock(side_effect=[records, []]))
    paid = Mock(side_effect=AssertionError("Must not spend Apify budget"))
    monkeypatch.setattr(ali1688.Apify1688Connector, "fetch_ads", paid)
    assert len(list(c.fetch_ads(FetchParams(query="x", limit=40)))) == 20
    paid.assert_not_called()


def test_paid_fallback_is_deduplicated_and_bounded_by_remaining_limit(monkeypatch):
    c = ali1688.Ali1688Connector({"apify_fallback": {}})
    record = lambda oid: ali1688.parse_offers({"data": {str(oid): {"title": "Bracelet"}}}, "x", 20)[0]
    monkeypatch.setattr(c, "search", lambda *a: [record(100)])
    paid = Mock(return_value=iter([record(100), record(101), record(101), record(102), record(103)]))
    monkeypatch.setattr(ali1688.Apify1688Connector, "fetch_ads", paid)
    rows = list(c.fetch_ads(FetchParams(query="x", limit=3)))
    assert [r.source_ad_id for r in rows] == ["100", "101", "102"]
    assert paid.call_args.args[0].limit == 2
