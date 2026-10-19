"""Phase 8 API: the MKT's own Library (app/library.py). Result update and "trả về kho" reuse the allocation endpoints."""
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import library as L
from ..db import get_db
from ..workspace.auth import current_user

router = APIRouter(prefix="/api/library", tags=["library"], dependencies=[Depends(current_user)])


class ToggleIn(BaseModel):
    on: bool = True


@router.get("")
def library(section: Literal["my", "saved", "favorite", "testing", "tested", "similar", "followed"] = "my",
            category: str | None = None, market: str | None = None, product_type: str | None = None,
            test_status: str | None = None, advertiser_id: int | None = None, min_fit: float | None = None,
            limit: int = Query(24, ge=1, le=100), offset: int = Query(0, ge=0),
            user=Depends(current_user), db: Session = Depends(get_db)):
    # always the session's own user: there is no user_id parameter (private workspace)
    return L.library(db, user["id"], section, category, market, product_type, test_status, advertiser_id, min_fit, limit, offset)


@router.get("/dashboard")
def dashboard(user=Depends(current_user), db: Session = Depends(get_db)):
    """Personal dashboard (Phase 9): the session's own numbers only."""
    return L.dashboard(db, user["id"])


@router.post("/products/{pid}/save")
def save(pid: int, body: ToggleIn, user=Depends(current_user), db: Session = Depends(get_db)):
    root = L.toggle(db, user["id"], pid, "saved", body.on)
    db.commit()
    return {"product_id": root, "saved": body.on}


@router.post("/products/{pid}/favorite")
def favorite(pid: int, body: ToggleIn, user=Depends(current_user), db: Session = Depends(get_db)):
    root = L.toggle(db, user["id"], pid, "favorite", body.on)
    db.commit()
    return {"product_id": root, "favorite": body.on}


@router.post("/products/{pid}/start-test")
def start_test(pid: int, user=Depends(current_user), db: Session = Depends(get_db)):
    a = L.start_test(db, user["id"], pid)
    db.commit()
    return {"allocation_id": a.id, "status": a.status, "product_id": a.product_id}
