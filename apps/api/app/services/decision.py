"""Lifecycle state machine (§14)."""
from datetime import datetime

from sqlalchemy.orm import Session

from ..models import LifecycleEvent, Product

TRANSITIONS = {
    "DISCOVERED": {"WATCHLIST", "CANDIDATE", "TESTING", "RETIRE"},
    "WATCHLIST": {"CANDIDATE", "TESTING", "RETIRE", "DISCOVERED"},
    "CANDIDATE": {"TESTING", "WATCHLIST", "RETIRE"},
    "TESTING": {"FAIL", "HOLD", "WIN"},
    "FAIL": {"TESTING", "RETIRE", "WATCHLIST"},
    "HOLD": {"TESTING", "WIN", "FAIL", "RETIRE"},
    "WIN": {"SCALE", "HOLD", "SATURATED", "TESTING"},
    "SCALE": {"SATURATED", "HOLD", "RETIRE"},
    "SATURATED": {"RETIRE", "SCALE"},
    "RETIRE": {"WATCHLIST", "TESTING"},
}
LIFECYCLE_STATES = list(TRANSITIONS)


def transition(db: Session, product: Product, to: str, reason: str, force: bool = False) -> bool:
    cur = product.lifecycle_status
    if cur == to:
        return False
    if not force and to not in TRANSITIONS.get(cur, set()):
        raise ValueError(f"Không thể chuyển {cur} → {to}. Hợp lệ: {sorted(TRANSITIONS.get(cur, []))}")
    product.lifecycle_status = to
    db.add(LifecycleEvent(product_id=product.id, from_status=cur, to_status=to, reason=reason, created_at=datetime.utcnow()))
    return True


def auto_lifecycle(db: Session, product: Product):
    """Automatic moves driven by market scores. Manual RETIRE is never undone."""
    for _ in range(2):  # e.g. DISCOVERED → WATCHLIST → CANDIDATE in one pass
        s = product.lifecycle_status
        if s == "RETIRE":
            return
        try:
            if s in ("WIN", "SCALE") and product.saturation_state in ("Highly Saturated", "Declining"):
                changed = transition(db, product, "SATURATED", f"Saturation {product.saturation_score:.0f} ({product.saturation_state})")
            elif s in ("DISCOVERED", "WATCHLIST") and product.recommendation in ("TEST", "TEST_NOW"):
                changed = transition(db, product, "CANDIDATE", "Decision engine → TEST")
            elif s == "DISCOVERED" and product.opportunity_score >= 50:
                changed = transition(db, product, "WATCHLIST", f"Opportunity {product.opportunity_score:.0f}")
            else:
                changed = False
        except ValueError:
            changed = False
        if not changed:
            return
