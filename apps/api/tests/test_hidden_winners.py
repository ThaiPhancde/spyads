"""Hidden Winner counts and pagination use the same filtered result set."""
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.db import get_db
from app.main import hidden_winners
from app.routers import intel
from app.services import hidden


@pytest.fixture
def client(monkeypatch):
    rows = [{"product": SimpleNamespace(id=i), "status": "CONFIRMED",
             "tags": ["rare"] if i < 55 else ["scaling"]} for i in range(73)]
    monkeypatch.setattr(hidden, "compute", lambda db, scope, **kw: rows if scope != "VN" else rows[:3])
    monkeypatch.setattr(intel, "cached", lambda key, fn: fn())
    monkeypatch.setattr(intel, "covers", lambda db, prods: {})
    monkeypatch.setattr(intel, "landing_links", lambda db, prods: {})
    monkeypatch.setattr(intel, "product_card", lambda p, cover: {"id": p.id})
    app = FastAPI()
    app.get("/hidden")(hidden_winners)
    app.dependency_overrides[get_db] = lambda: None
    return TestClient(app)


@pytest.mark.parametrize("tag,total", [(None, 73), ("rare", 55), ("scaling", 18), ("unknown", 0)])
def test_counts_and_every_page(client, tag, total):
    params = {"tag": tag} if tag else {}
    seen = []
    while True:
        response = client.get("/hidden", params={**params, "offset": len(seen)})
        assert response.status_code == 200
        page = response.json()
        assert page["total"] == total
        assert page["all_total"] == 73
        assert page["tags"] == {"rare": 55, "scaling": 18}
        assert len(page["rows"]) <= 50
        seen.extend(row["id"] for row in page["rows"])
        assert page["has_more"] == (len(seen) < total)
        if not page["has_more"]:
            break
        assert page["rows"]
    assert len(seen) == len(set(seen)) == total
    if tag:
        assert all(tag in row["tags"] for row in page["rows"])
    assert client.get("/hidden", params={**params, "offset": total}).json()["rows"] == []


def test_scope_counts(client):
    page = client.get("/hidden", params={"scope": "VN", "tag": "scaling"}).json()
    assert page["all_total"] == 3
    assert page["total"] == 0
    assert page["tags"] == {"rare": 3}
    assert page["rows"] == []
    assert page["has_more"] is False


@pytest.mark.parametrize("params", [{"offset": -1}, {"limit": 0}, {"limit": 501}])
def test_invalid_pagination(client, params):
    assert client.get("/hidden", params=params).status_code == 422
