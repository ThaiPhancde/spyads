"""One scoring system for MKT: Opportunity v2 + TEST_NOW / TEST / WATCH / SKIP. Win / Rare Winner (v1) stay in the DB but
never reach an API payload, a filter, a sort, a KPI or an explanation."""
from datetime import datetime
import pytest

pytestmark = pytest.mark.usefixtures("clean_catalog")

from app import main
from app.db import SessionLocal
from app.models import Product
from app.routers import intel
from app.services import agent
from app.services.engine import is_test_candidate

V1 = {"win_score", "external_win_score", "rare_winner_score", "win_label", "internal_win_score", "customer_rejection_score"}


def _p(rec, opp=69.2, win=24.3, **kw):
    """PRD_0000001 as found 2026-10-13: Win 24 (v1 "Weak") next to Opportunity 69 / TEST (v2)."""
    return Product(id=1, product_code="PRD_0000001", canonical_name="Demo", recommendation=rec, opportunity_score=opp, win_score=win,
                   external_win_score=win, rare_winner_score=10, confidence_score=55, saturation_score=29, saturation_state="Low Saturation",
                   recommendation_reasons=["why"], features={}, potential={"vector": {"opportunity": opp, "opportunity_raw": opp - 5}},
                   first_seen_at=datetime.utcnow(), last_seen_at=datetime.utcnow(), **kw)


def test_test_candidate_is_the_v2_label_not_win():
    assert is_test_candidate(_p("TEST")) and is_test_candidate(_p("TEST_NOW"))
    assert not is_test_candidate(_p("WATCH", win=90)) and not is_test_candidate(_p("SKIP", win=90))  # a high v1 Win decides nothing


def test_no_v1_field_in_any_product_card():
    p = _p("TEST")
    for card in (main.product_card(p), intel.product_card(p)):
        assert not V1 & card.keys(), V1 & card.keys()
        assert card["opportunity_score" if "opportunity_score" in card else "opportunity"] == 69.2 and card["recommendation"] == "TEST"


def test_explanation_speaks_v2_only():
    text = agent.explain_product(_p("TEST"))
    assert "Opportunity 69" in text and "TEST" in text and "Win" not in text and "Weak" not in text


def test_agent_win_phrases_map_to_opportunity():
    spec = agent.parse_rules("sản phẩm win score cao ở PH")
    assert spec["min_opportunity"] == 50 and "min_win_score" not in spec


def test_list_products_ignores_v1_sort_and_filter():
    with SessionLocal() as db:
        db.add_all([Product(product_code=f"T{i}", canonical_name=f"v1sort_{i}", in_target=True, opportunity_score=o, win_score=w,
                            first_seen_at=datetime.utcnow(), last_seen_at=datetime.utcnow(), features={})
                    for i, (o, w) in enumerate([(70, 10), (20, 99)])])
        db.commit()
        for sort in ("win_score", "rare_winner_score", "opportunity_score"):
            rows = main.list_products(q="v1sort_", sort=sort, limit=50, offset=0, db=db)["rows"]
            assert [r["opportunity_score"] for r in rows][:2] == [70, 20], sort  # Opportunity order, not Win order
            assert not V1 & rows[0].keys()
        import inspect
        assert "min_win" not in inspect.signature(main.list_products).parameters


def test_dashboard_kpi_is_test_candidates():
    with SessionLocal() as db:
        db.add_all([Product(product_code=f"K{i}", canonical_name=f"k{i}", recommendation=r, category="x", in_target=True,
                            first_seen_at=datetime.utcnow(), last_seen_at=datetime.utcnow(), features={}, win_score=w)
                    for i, (r, w) in enumerate([("TEST", 1), ("TEST_NOW", 1), ("WATCH", 99), ("SKIP", 99)])])
        db.commit()
        k = main._pulse(None, db)["kpis"]
        assert "potential_winners" not in k and k["test_candidates"] >= 2
        n = sum(1 for p in db.query(Product) if p.recommendation in ("TEST", "TEST_NOW"))
        assert k["test_candidates"] == n  # unique products, one per row
