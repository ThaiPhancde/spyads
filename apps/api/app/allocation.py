"""Phase 2: exclusive product allocation on top of the Phase 1 cluster overlay.

One row in `product_allocation_locks` per owned product, keyed by product_id: the primary key IS the guarantee that two
MKTs never own the same product, even when two claims race in separate transactions. A confident cluster is claimed as a
whole (all of its products locked in one transaction, all-or-nothing); a low-confidence cluster only locks the product
that was asked for. Nothing here runs on search/listing reads.
"""
import os
from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import JSON, CheckConstraint, DateTime, Float, ForeignKey, Integer, String, Text, delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column

from .cluster_models import AdClusterMembership, ProductCluster
from .db import Base
from .models import Ad, Product

ACTIVE = ("reserved", "claimed", "testing", "tested")
STATUSES = ACTIVE + ("released",)
# Allowed owner-driven moves; release is separate and allowed from every active state.
TRANSITIONS = {"reserved": {"claimed", "testing"}, "claimed": {"testing", "tested"},
               "testing": {"claimed", "tested"}, "tested": {"testing"}}
OUTCOMES = ("winner", "loser", "inconclusive")


def ttl():
    return timedelta(hours=float(os.getenv("ALLOCATION_RESERVE_HOURS", "48")))


def group_min():
    return float(os.getenv("ALLOCATION_GROUP_MIN_CONFIDENCE", os.getenv("CLUSTER_AUTO_AT", "0.93")))


class ProductAllocation(Base):
    __tablename__ = "product_allocations"
    __table_args__ = (CheckConstraint("status IN ('reserved','claimed','testing','tested','released')"),
                      CheckConstraint("scope IN ('cluster','product')"))
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)  # the product the MKT asked for
    cluster_id: Mapped[int] = mapped_column(ForeignKey("product_clusters.id"), index=True)  # root at claim time
    scope: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), index=True)
    reserved_until: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    release_reason: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    released_at: Mapped[datetime | None] = mapped_column(DateTime)


class AllocationLock(Base):
    __tablename__ = "product_allocation_locks"
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), primary_key=True)
    allocation_id: Mapped[int] = mapped_column(ForeignKey("product_allocations.id"), index=True)


class ProductTestResult(Base):
    __tablename__ = "product_test_results"
    __table_args__ = (CheckConstraint("outcome IN ('winner','loser','inconclusive')"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    allocation_id: Mapped[int] = mapped_column(ForeignKey("product_allocations.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)  # who ran it; survives release
    market: Mapped[str | None] = mapped_column(String(8))
    outcome: Mapped[str] = mapped_column(String(16))
    spend: Mapped[float | None] = mapped_column(Float)
    orders: Mapped[int | None] = mapped_column(Integer)
    revenue: Mapped[float | None] = mapped_column(Float)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AllocationAudit(Base):
    __tablename__ = "product_allocation_audit"
    id: Mapped[int] = mapped_column(primary_key=True)
    allocation_id: Mapped[int] = mapped_column(ForeignKey("product_allocations.id"), index=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))  # NULL = system (TTL expiry)
    action: Mapped[str] = mapped_column(String(24))
    from_status: Mapped[str | None] = mapped_column(String(16))
    to_status: Mapped[str | None] = mapped_column(String(16))
    from_user_id: Mapped[int | None] = mapped_column(Integer)
    to_user_id: Mapped[int | None] = mapped_column(Integer)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


def audit(db, a, actor, action, before=None, from_user=None, **data):
    db.add(AllocationAudit(allocation_id=a.id, actor_id=actor, action=action, from_status=before, to_status=a.status,
                           from_user_id=from_user, to_user_id=a.user_id, data=data))


def root_of(db, pid):
    seen = set()
    node = db.get(ProductCluster, pid)
    while node is not None and node.merged_into_id is not None:
        if node.id in seen:
            raise ValueError("Cluster redirect cycle")
        seen.add(node.id)
        node = db.get(ProductCluster, node.merged_into_id)
    return node.id if node is not None else pid


def scope_of(db, pid):
    """(root, products to lock, scope, confident). Low-confidence groups are never locked as a whole."""
    root = root_of(db, pid)
    rows = db.execute(select(AdClusterMembership.source_product_id, AdClusterMembership.method,
                             AdClusterMembership.confidence).where(AdClusterMembership.cluster_id == root)).all()
    products = {root, pid} | {r.source_product_id for r in rows}
    # Products merged into the root carry the merge confidence; a manual MKT review decision counts as confident.
    confident = all(r.method == "manual" or (r.confidence is not None and r.confidence >= group_min())
                    for r in rows if r.source_product_id != root)
    if len(products) > 1 and not confident:
        return root, {pid}, "product", False
    return root, products, "cluster", True


def expire(db, now=None):
    """Release reservations past their TTL. Set-based so concurrent callers can't double-release."""
    now = now or datetime.utcnow()
    gone = db.execute(update(ProductAllocation)
                      .where(ProductAllocation.status == "reserved", ProductAllocation.reserved_until < now)
                      .values(status="released", release_reason="expired", released_at=now, updated_at=now)
                      .returning(ProductAllocation.id, ProductAllocation.user_id)
                      .execution_options(synchronize_session=False)).all()
    if gone:
        ids = [g.id for g in gone]
        db.execute(delete(AllocationLock).where(AllocationLock.allocation_id.in_(ids)))
        for g in gone:
            db.add(AllocationAudit(allocation_id=g.id, actor_id=None, action="expire", from_status="reserved",
                                   to_status="released", from_user_id=g.user_id, to_user_id=g.user_id, data={}))
    return len(gone)


def owners(db, products):
    return db.execute(select(AllocationLock.product_id, ProductAllocation)
                      .join(ProductAllocation, ProductAllocation.id == AllocationLock.allocation_id)
                      .where(AllocationLock.product_id.in_(products))).all()


def lock(db, a, products):
    """Insert locks in id order (no deadlock between overlapping claims); the PK rejects any second owner."""
    try:
        for p in sorted(products):
            db.add(AllocationLock(product_id=p, allocation_id=a.id))
            db.flush()
    except IntegrityError:
        db.rollback()
        taken(db, a.user_id, a.product_id, None)


def taken(db, user_id, pid, state):
    """409 for a product another MKT owns: their state (testing / claimed …), never who; plus open look-alikes."""
    from . import similar as S
    msg = ("Sản phẩm đang được MKT khác test" if state == "testing" else
           "Sản phẩm đã được MKT khác nhận" if state else "Sản phẩm vừa được MKT khác nhận")
    try:
        alt = [{"product_id": r["product_id"], "name": r["name"], "score": r["score"]}
               for r in (S.similar_products(db, pid, user_id, 5, exclusive=True) or {}).get("similar", [])]
    except Exception:  # noqa: BLE001 — suggestions are a courtesy; the refusal itself must always go out
        alt = []
    raise HTTPException(409, {"code": "taken", "state": state or "claimed", "similar": alt,
                              "message": msg + (" — xem sản phẩm tương tự còn trống." if alt else ".")})


def claim(db, user_id, pid):
    """Atomic reserve. Caller commits; on conflict the transaction is rolled back and 409 raised."""
    if not db.get(Product, pid):
        raise HTTPException(404, "Không tìm thấy sản phẩm")
    expire(db)
    root, products, scope, _ = scope_of(db, pid)
    if db.get(ProductCluster, root) is None:  # product not yet seen by cluster sync: it is its own cluster
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert
        insert = sqlite_insert if db.bind.dialect.name == "sqlite" else pg_insert
        db.execute(insert(ProductCluster).values(id=root, revision=0, created_at=datetime.utcnow()).on_conflict_do_nothing())
    held = owners(db, products)
    other = next((a for _, a in held if a.user_id != user_id), None)
    if other is not None:
        taken(db, user_id, pid, other.status)
    mine = {a.id: a for _, a in held}
    if len(mine) == 1:  # already mine: idempotent, and pick up products that joined the cluster since
        a = next(iter(mine.values()))
        missing = products - {p for p, _ in held}
        if missing:
            lock(db, a, missing)
            audit(db, a, user_id, "extend", a.status, a.user_id, products=sorted(missing))
        return a
    now = datetime.utcnow()
    a = ProductAllocation(user_id=user_id, product_id=pid, cluster_id=root, scope=scope, status="reserved",
                          reserved_until=now + ttl(), created_at=now, updated_at=now)
    db.add(a)
    db.flush()
    lock(db, a, products - {p for p, _ in held})
    audit(db, a, user_id, "claim", None, None, products=sorted(products), scope=scope)
    return a


def owned(db, aid, user):
    a = db.scalar(select(ProductAllocation).where(ProductAllocation.id == aid).with_for_update())
    if not a or a.user_id != user["id"]:
        raise HTTPException(404, "Không tìm thấy quyền sở hữu trong tài khoản của bạn")
    if a.status not in ACTIVE:
        raise HTTPException(409, "Quyền sở hữu đã được giải phóng")
    if a.status == "reserved" and a.reserved_until < datetime.utcnow():
        raise HTTPException(409, "Giữ chỗ đã hết hạn")
    return a


def set_status(db, a, actor, status):
    if status not in TRANSITIONS.get(a.status, ()):
        raise HTTPException(409, f"Không thể chuyển {a.status} → {status}")
    before = a.status
    a.status, a.reserved_until, a.updated_at = status, None, datetime.utcnow()
    audit(db, a, actor, "status", before, a.user_id)
    return a


def release(db, a, actor, reason="released"):
    before = a.status
    db.execute(delete(AllocationLock).where(AllocationLock.allocation_id == a.id))
    a.status, a.release_reason, a.reserved_until = "released", reason, None
    a.released_at = a.updated_at = datetime.utcnow()
    audit(db, a, actor, "release", before, a.user_id, reason=reason)
    return a


def record_result(db, a, actor, outcome, market=None, spend=None, orders=None, revenue=None, note=""):
    r = ProductTestResult(allocation_id=a.id, user_id=actor, outcome=outcome, market=market, spend=spend,
                          orders=orders, revenue=revenue, note=note or "")
    db.add(r)
    before = a.status
    a.status, a.reserved_until, a.updated_at = "tested", None, datetime.utcnow()
    db.flush()
    audit(db, a, actor, "result", before, a.user_id, result_id=r.id, outcome=outcome)
    return r


def on_interaction(db, user_id, ad_id, saved=None, favorite=None, tested=None):
    """Account policy 2026-10-19: Save / Favorite are soft interest signals (app/interest.py) and never create, extend
    or convert ownership — a reservation keeps its TTL. Only marking an ad "tested" on a product the MKT already owns
    moves that allocation to `testing`. Never creates an allocation by itself (that needs Nhận / Bắt đầu test)."""
    if not tested:
        return None
    ad = db.get(Ad, ad_id)
    lk = db.get(AllocationLock, ad.product_id) if ad and ad.product_id else None
    a = db.get(ProductAllocation, lk.allocation_id) if lk else None
    if not a or a.user_id != user_id:
        return None
    if a.status in ("reserved", "claimed") and not (a.status == "reserved" and a.reserved_until < datetime.utcnow()):
        return set_status(db, a, user_id, "testing")
    return a


def status(db, user, pid):
    """Read-only view; an expired reservation already reads as available before the next write sweeps it."""
    root, products, scope, confident = scope_of(db, pid)
    now = datetime.utcnow()
    live = [(p, a) for p, a in owners(db, products)
            if not (a.status == "reserved" and a.reserved_until < now)]
    own = next((a for p, a in live if p == pid), None)
    if own is None and scope == "cluster":
        own = next((a for _, a in live), None)
    mine = bool(own and own.user_id == user["id"])
    # another MKT's allocation shows its state only (taken / testing …): never who, never its id
    return {"product_id": pid, "cluster_id": root, "scope": scope, "group_confident": confident,
            "status": own.status if own else "available", "allocation_id": own.id if mine else None,
            "mine": mine, "reserved_until": own.reserved_until if own else None,
            "conflict": len({a.user_id for _, a in live}) > 1 and scope == "cluster"}


def view(db, a, detail=False):
    out = {"id": a.id, "user_id": a.user_id, "product_id": a.product_id, "cluster_id": a.cluster_id, "scope": a.scope,
           "status": a.status, "reserved_until": a.reserved_until, "release_reason": a.release_reason,
           "created_at": a.created_at, "updated_at": a.updated_at, "released_at": a.released_at,
           "products": sorted(db.scalars(select(AllocationLock.product_id).where(AllocationLock.allocation_id == a.id)))}
    if detail:
        out["results"] = [{"id": r.id, "user_id": r.user_id, "market": r.market, "outcome": r.outcome, "spend": r.spend,
                           "orders": r.orders, "revenue": r.revenue, "note": r.note, "created_at": r.created_at}
                          for r in db.scalars(select(ProductTestResult).where(ProductTestResult.allocation_id == a.id)
                                              .order_by(ProductTestResult.id))]
        out["audit"] = [{"action": x.action, "actor_id": x.actor_id, "from_status": x.from_status, "to_status": x.to_status,
                         "from_user_id": x.from_user_id, "to_user_id": x.to_user_id, "data": x.data, "created_at": x.created_at}
                        for x in db.scalars(select(AllocationAudit).where(AllocationAudit.allocation_id == a.id)
                                            .order_by(AllocationAudit.id))]
    return out
