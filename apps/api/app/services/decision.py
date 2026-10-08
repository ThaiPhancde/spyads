"""Decision Engine (§28), Test failure analysis (§13), Lifecycle state machine (§14)
and Learning Loop (§30)."""
from collections import defaultdict
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Experiment, LifecycleEvent, Product
from .enrichment import price_segment

# ============================================================ Thresholds (tunable)
TH = {
    "scale_internal": 72, "scale_margin": 0.12, "scale_refusal": 0.22, "scale_delivery": 0.70,
    "hold_roas": 2.0, "hold_refusal": 0.25,
    "low_ctr": 0.008, "high_cpa_ratio": 0.45, "low_cvr": 0.012,
    "stop_external": 45, "stop_internal": 45,
}

# Funnel benchmarks used by failure analysis
BENCH = {
    "ctr": 0.010, "atc_rate": 0.06, "checkout_rate": 0.40, "purchase_rate": 0.45,
    "confirm_rate": 0.70, "delivery_rate": 0.72, "refusal_rate": 0.22, "return_rate": 0.10,
    "ad_rejection_rate": 0.30, "min_spend": 150, "min_impressions": 8000,
}

FAILURE_TYPES = [
    "Creative Failure", "Product-Market Mismatch", "Price Problem", "Landing Page Problem", "Offer Problem",
    "Checkout Problem", "Low Lead Quality", "Telesales Failure", "Logistics Failure", "High Refusal",
    "High Return", "Compliance Failure",
]


def _r(a, b):
    return a / b if b else None


# ============================================================ §12-13 Experiment analysis
def experiment_metrics(e: Experiment) -> dict:
    return {
        "ctr": _r(e.clicks, e.impressions),
        "cpc": _r(e.spend, e.clicks),
        "lp_rate": _r(e.landing_views, e.clicks),
        "atc_rate": _r(e.atc, e.landing_views or e.clicks),
        "checkout_rate": _r(e.checkout, e.atc),
        "purchase_rate": _r(e.purchase, e.checkout),
        "cvr": _r(e.purchase, e.landing_views or e.clicks),
        "cpa": _r(e.spend, e.purchase),
        "roas": _r(e.revenue, e.spend),
        "aov": _r(e.revenue, e.purchase),
        "confirm_rate": _r(e.confirmed_orders, e.purchase),
        "delivery_rate": _r(e.delivered, e.shipped),
        "refusal_rate": _r(e.refused, e.delivered + e.refused),
        "return_rate": _r(e.returned, e.delivered),
        "ad_rejection_rate": _r(e.ads_rejected, e.ads_submitted),
    }


def analyze_experiment(e: Experiment, market_avg_price: float | None = None, negative_mismatch: float = 0) -> dict:
    """Walk the funnel top→bottom and attribute the failure to the first broken stage (§13)."""
    m = experiment_metrics(e)
    diag: list[dict] = []
    failures: list[str] = []

    def add(stage, ok, value, bench, failure=None, note=""):
        diag.append({"stage": stage, "ok": ok, "value": None if value is None else round(value, 4),
                     "benchmark": bench, "failure": None if ok else failure, "note": note})
        if not ok and failure:
            for f in failure if isinstance(failure, list) else [failure]:
                if f not in failures:
                    failures.append(f)

    if (m["ad_rejection_rate"] or 0) > BENCH["ad_rejection_rate"]:
        add("Ad approval", False, m["ad_rejection_rate"], BENCH["ad_rejection_rate"], "Compliance Failure",
            "Nhiều creative bị từ chối — kiểm tra claim y tế / before-after.")

    if e.spend < BENCH["min_spend"] or e.impressions < BENCH["min_impressions"]:
        status = "WAITING"
        add("Data", False, e.spend, BENCH["min_spend"], None, "Chưa đủ spend/impression để kết luận.")
        return {"status": status, "failure_types": failures, "diagnosis": diag, "metrics": m, "decision": "WAIT DATA"}

    stages = [
        ("CTR", m["ctr"], BENCH["ctr"], "Creative Failure", "CTR thấp → vấn đề creative / hook."),
        ("Add to cart", m["atc_rate"], BENCH["atc_rate"], None, "CTR tốt nhưng ATC thấp → product / landing / price."),
        ("Checkout", m["checkout_rate"], BENCH["checkout_rate"], "Offer Problem", "ATC tốt nhưng checkout thấp → offer / trust."),
        ("Purchase", m["purchase_rate"], BENCH["purchase_rate"], "Checkout Problem", "Checkout tốt nhưng purchase thấp → payment / checkout."),
        ("Confirmation", m["confirm_rate"], BENCH["confirm_rate"], ["Low Lead Quality", "Telesales Failure"], "Purchase tốt nhưng xác nhận thấp → lead quality / telesales."),
        ("Delivery", m["delivery_rate"], BENCH["delivery_rate"], "Logistics Failure", "Xác nhận tốt nhưng giao thấp → COD / logistics."),
    ]
    for name, val, bench, failure, note in stages:
        if val is None:
            continue
        ok = val >= bench * {"Add to cart": 0.6, "Confirmation": 0.85, "Delivery": 0.85}.get(name, 0.75)
        if name == "Add to cart" and not ok:
            failure = []
            if market_avg_price and e.sell_price and e.sell_price > market_avg_price * 1.2:
                failure.append("Price Problem")
            if negative_mismatch > 0.15 or val < bench * 0.3:
                failure.append("Product-Market Mismatch")
            if not failure or (m["lp_rate"] or 1) < 0.6:
                failure.append("Landing Page Problem")
        add(name, ok, val, bench, failure, note)

    if (m["refusal_rate"] or 0) > BENCH["refusal_rate"]:
        add("COD refusal", False, m["refusal_rate"], BENCH["refusal_rate"], "High Refusal", "Tỷ lệ khách từ chối nhận hàng cao.")
    if (m["return_rate"] or 0) > BENCH["return_rate"]:
        add("Returns", False, m["return_rate"], BENCH["return_rate"], "High Return", "Tỷ lệ hoàn/đổi trả cao.")

    roas = m["roas"] or 0
    healthy_ops = (m["delivery_rate"] or 1) >= BENCH["delivery_rate"] * 0.9 and (m["refusal_rate"] or 0) <= BENCH["refusal_rate"]
    if roas >= 2.5 and healthy_ops:
        status, decision = "WIN", "SCALE"
    elif roas >= 2.0 and not healthy_ops:
        status, decision = "PROMISING", "HOLD / FIX OPERATIONS"
    elif roas >= 1.5:
        status, decision = "PROMISING", "ITERATE"
    else:
        status = "FAILED"
        if "Creative Failure" in failures:
            decision = "ITERATE CREATIVE"
        elif any(f in failures for f in ("Price Problem", "Landing Page Problem", "Offer Problem", "Product-Market Mismatch")):
            decision = "FIX PRODUCT / LANDING / OFFER"
        else:
            decision = "STOP"
    return {"status": status, "failure_types": failures, "diagnosis": diag, "metrics": m, "decision": decision}


# ============================================================ §28 Decision engine
def recommend(product: Product, f: dict) -> tuple[str, list[str]]:
    """After a test (company data): SCALE · HOLD · ITERATE · STOP, or WATCH while data is thin.
    Before a test the decision comes from discovery.discovery_decision (TEST_NOW · TEST · WATCH · REVIEW · SKIP)."""
    ext, internal = product.external_win_score, product.internal_win_score

    if f.get("has_internal") and internal is not None:
        cm = f.get("contribution_margin")
        cm = -1 if cm is None else cm  # 0.0 margin is a number, not "unknown"
        refusal = f.get("refusal_rate") or 0
        roas, ctr, cvr = f.get("roas") or 0, f.get("ctr") or 0, f.get("cvr") or 0
        cpa_ratio = (f.get("cpa") or 0) / (f.get("aov") or 1)
        delivery = f.get("delivery_rate") or 0
        if internal >= TH["scale_internal"] and cm >= TH["scale_margin"] and refusal < TH["scale_refusal"] and delivery >= TH["scale_delivery"]:
            return "SCALE", [f"Internal Win {internal:.0f} ≥ {TH['scale_internal']}", f"Contribution margin {cm:.0%}", f"Refusal {refusal:.0%} thấp"]
        if roas >= TH["hold_roas"] and refusal >= TH["hold_refusal"]:
            return "HOLD", [f"ROAS {roas:.1f} tốt nhưng refusal {refusal:.0%} cao", "→ FIX OPERATIONS (xác nhận đơn, COD, logistics)"]
        if ctr and ctr < TH["low_ctr"]:
            return "ITERATE", [f"CTR {ctr:.2%} < {TH['low_ctr']:.1%}", "→ ITERATE CREATIVE (hook/angle mới)"]
        if cpa_ratio > TH["high_cpa_ratio"] and cvr < TH["low_cvr"]:
            return "ITERATE", [f"CPA = {cpa_ratio:.0%} AOV, CVR {cvr:.2%}", "→ FIX PRODUCT / LANDING / OFFER"]
        if ext < TH["stop_external"] and internal < TH["stop_internal"]:
            return "STOP", [f"External {ext:.0f} và Internal {internal:.0f} đều thấp"]
        if internal < 30 and roas < 1:
            return "STOP", [f"Internal {internal:.0f}, ROAS {roas:.2f} < 1 — lỗ"]
        if internal >= TH["scale_internal"] and delivery < TH["scale_delivery"]:
            return "HOLD", [f"Internal Win {internal:.0f} nhưng delivery {delivery:.0%} thấp", "→ FIX OPERATIONS trước khi scale"]
        if internal >= 60:
            return "HOLD", [f"Internal Win {internal:.0f} khá nhưng chưa đủ điều kiện scale", f"Margin {cm:.0%}, refusal {refusal:.0%}"]
        return "WATCH", [f"Internal Win {internal:.0f} — chờ thêm dữ liệu"]
    return "WATCH", ["Chưa có dữ liệu nội bộ — dùng quyết định discovery"]


# ============================================================ §14 Lifecycle state machine
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


def auto_lifecycle(db: Session, product: Product, f: dict, exps: list[Experiment]):
    """Automatic moves driven by scores and experiment outcomes. Manual RETIRE is never undone."""
    running = [e for e in exps if e.ended_at is None]
    done = [e for e in exps if e.ended_at is not None and e.status in ("WIN", "PROMISING", "FAILED")]
    won = any(e.status == "WIN" for e in exps if e.ended_at is not None)
    for _ in range(4):  # walk several edges in one pass, e.g. DISCOVERED → TESTING → WIN → SCALE
        s = product.lifecycle_status
        if s == "RETIRE":
            return
        try:
            if s in ("DISCOVERED", "WATCHLIST", "CANDIDATE") and (running or done):
                changed = transition(db, product, "TESTING", "Experiment được tạo")
            elif s in ("FAIL", "HOLD") and running:
                changed = transition(db, product, "TESTING", "Test lại")
            elif s == "TESTING" and won:
                changed = transition(db, product, "WIN", "Có experiment WIN")
            elif s == "TESTING" and done and not running:
                best = "HOLD" if any(e.status == "PROMISING" for e in done) else "FAIL"
                changed = transition(db, product, best, f"Kết quả test: {best}")
            elif s == "WIN" and product.recommendation == "SCALE":
                changed = transition(db, product, "SCALE", "Decision engine → SCALE")
            elif s in ("WIN", "SCALE") and product.saturation_state in ("Highly Saturated", "Declining"):
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


# ============================================================ §30 Learning loop
def learning_profile(db: Session) -> dict:
    """Aggregate historical experiments into company-fit segments."""
    exps = db.scalars(select(Experiment).where(Experiment.ended_at.is_not(None), Experiment.status.in_(["WIN", "PROMISING", "FAILED"]))).all()
    products = {p.id: p for p in db.scalars(select(Product)).all()}

    dims = {
        "market": lambda e, p: e.market,
        "category": lambda e, p: p.category,
        "price_segment": lambda e, p: price_segment(e.sell_price or p.price, p.currency),
        "creative_type": lambda e, p: e.creative_type,
        "angle": lambda e, p: e.angle,
        "funnel": lambda e, p: e.funnel,
        "platform": lambda e, p: e.platform,
    }
    seg: dict[str, dict[str, dict]] = {d: defaultdict(lambda: defaultdict(float)) for d in dims}
    combos: dict[tuple, dict] = defaultdict(lambda: defaultdict(float))
    for e in exps:
        p = products.get(e.product_id)
        if not p:
            continue
        cogs = (e.unit_cost or 0) * e.delivered
        rev_deliv = (e.revenue / e.purchase * e.delivered) if e.purchase else 0
        profit = rev_deliv - cogs - e.spend
        rows = [seg[d][fn(e, p) or "unknown"] for d, fn in dims.items()]
        rows.append(combos[(e.market, p.category, price_segment(e.sell_price or p.price, p.currency), e.creative_type, e.funnel)])
        for r in rows:
            r["tests"] += 1
            r["wins"] += 1 if e.status == "WIN" else 0
            r["spend"] += e.spend
            r["purchase"] += e.purchase
            r["clicks"] += e.landing_views or e.clicks
            r["delivered"] += e.delivered
            r["shipped"] += e.shipped
            r["revenue_delivered"] += rev_deliv
            r["profit"] += profit

    def fin(r):
        return {
            "tests": int(r["tests"]), "wins": int(r["wins"]),
            "win_rate": round(r["wins"] / r["tests"], 3) if r["tests"] else 0,
            "cvr": round(r["purchase"] / r["clicks"], 4) if r["clicks"] else None,
            "delivery_rate": round(r["delivered"] / r["shipped"], 3) if r["shipped"] else None,
            "margin": round(r["profit"] / r["revenue_delivered"], 3) if r["revenue_delivered"] else None,
            "profit": round(r["profit"], 2),
        }

    out = {d: sorted(({"value": k, **fin(v)} for k, v in rows.items()), key=lambda x: (-x["win_rate"], -x["tests"]))
           for d, rows in seg.items()}
    best = sorted(
        ({"market": k[0], "category": k[1], "price_segment": k[2], "creative_type": k[3], "funnel": k[4], **fin(v)}
         for k, v in combos.items() if v["tests"] >= 1),
        key=lambda x: (-(x["margin"] or -9) * x["win_rate"], -x["tests"]),
    )
    return {"total_experiments": len(exps), "segments": out, "winning_profiles": best[:8]}


def market_fit(profile: dict, country: str | None, category: str | None) -> float:
    """0-100: how well this market/category fits what the company has historically won."""
    def rate(dim, val):
        for r in profile["segments"].get(dim, []):
            if r["value"] == val and r["tests"] >= 2:
                return 30 + 70 * r["win_rate"]
        return None

    vals = [v for v in (rate("market", country), rate("category", category)) if v is not None]
    return round(sum(vals) / len(vals), 1) if vals else 60.0
