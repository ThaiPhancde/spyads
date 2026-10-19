"""Phase 2 API: claim / release / status of exclusive product ownership (app/allocation.py). Owners release their
own products; there is no ADMIN reassign (account policy 2026-10-19)."""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import allocation as A, behavior
from ..db import get_db
from ..workspace.auth import current_user

router = APIRouter(prefix="/api", tags=["allocations"], dependencies=[Depends(current_user)])


class ClaimIn(BaseModel):
    product_id: int


class ReleaseIn(BaseModel):
    reason: str = Field("released", max_length=32)


class StatusIn(BaseModel):
    status: Literal["claimed", "testing", "tested"]


class ResultIn(BaseModel):
    outcome: Literal["winner", "loser", "inconclusive"]
    market: str | None = Field(None, max_length=8)
    spend: float | None = Field(None, ge=0)
    orders: int | None = Field(None, ge=0)
    revenue: float | None = Field(None, ge=0)
    note: str = Field("", max_length=3000)


@router.post("/allocations/claim")
def claim(body: ClaimIn, user=Depends(current_user), db: Session = Depends(get_db)):
    a = A.claim(db, user["id"], body.product_id)
    db.commit()
    return A.view(db, a)


@router.post("/allocations/{aid}/release")
def release(aid: int, body: ReleaseIn, user=Depends(current_user), db: Session = Depends(get_db)):
    a = A.owned(db, aid, user)
    A.release(db, a, user["id"], body.reason)
    db.commit()
    return A.view(db, a)


@router.patch("/allocations/{aid}/status")
def set_status(aid: int, body: StatusIn, user=Depends(current_user), db: Session = Depends(get_db)):
    a = A.set_status(db, A.owned(db, aid, user), user["id"], body.status)
    if body.status == "testing":
        behavior.track(db, user["id"], "test_start", product_id=a.product_id, allocation_id=a.id)
    db.commit()
    return A.view(db, a)


@router.post("/allocations/{aid}/results")
def add_result(aid: int, body: ResultIn, user=Depends(current_user), db: Session = Depends(get_db)):
    a = A.owned(db, aid, user)
    r = A.record_result(db, a, user["id"], **body.model_dump())
    behavior.track(db, user["id"], behavior.OUTCOME_KIND[r.outcome], key=f"result:{r.id}", product_id=a.product_id,
                   allocation_id=a.id, result_id=r.id, market=r.market)
    db.commit()
    return {"result_id": r.id, **A.view(db, a, detail=True)}


@router.get("/allocations/status/{pid}")
def allocation_status(pid: int, user=Depends(current_user), db: Session = Depends(get_db)):
    if not db.get(A.Product, pid):
        raise HTTPException(404, "Không tìm thấy sản phẩm")
    return A.status(db, user, pid)


@router.get("/allocations")
def allocations(state: Literal["active", "released", "all"] = "active",
                limit: int = Query(40, ge=1, le=200), offset: int = Query(0, ge=0),
                user=Depends(current_user), db: Session = Depends(get_db)):
    q = select(A.ProductAllocation).where(A.ProductAllocation.user_id == user["id"])  # own allocations only
    if state != "all":
        q = q.where(A.ProductAllocation.status.in_(A.ACTIVE) if state == "active" else A.ProductAllocation.status == "released")
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    rows = db.scalars(q.order_by(A.ProductAllocation.updated_at.desc(), A.ProductAllocation.id.desc()).offset(offset).limit(limit))
    return {"total": total, "rows": [A.view(db, a) for a in rows]}


@router.get("/allocations/{aid}")
def allocation(aid: int, user=Depends(current_user), db: Session = Depends(get_db)):
    a = db.get(A.ProductAllocation, aid)
    if not a or a.user_id != user["id"]:
        raise HTTPException(404, "Không tìm thấy quyền sở hữu trong tài khoản của bạn")
    return A.view(db, a, detail=True)
