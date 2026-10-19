"""Phase 5 API: the MKT's personalized product feed, per-product explanation and on-demand refresh (app/recommend.py)."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from .. import recommend as R
from ..db import get_db
from ..workspace.auth import current_user, rate_limit

router = APIRouter(prefix="/api/recommendations", tags=["recommendations"], dependencies=[Depends(current_user)])


def target(user, user_id):
    if user_id is not None and user_id != user["id"]:  # another account's data is never readable
        raise HTTPException(404, "Không tìm thấy dữ liệu trong tài khoản của bạn")
    return user["id"]


@router.get("")
def feed(limit: int = Query(40, ge=1, le=200), offset: int = Query(0, ge=0), explore: bool = True,
         user_id: int | None = None, user=Depends(current_user), db: Session = Depends(get_db)):
    return R.feed(db, target(user, user_id), limit, offset, explore)


@router.get("/explain/{pid}")
def explain(pid: int, user_id: int | None = None, user=Depends(current_user), db: Session = Depends(get_db)):
    out = R.explain(db, target(user, user_id), pid)
    if out is None:
        raise HTTPException(404, "Không tìm thấy sản phẩm")
    return out


@router.post("/refresh")
def refresh(user=Depends(current_user), db: Session = Depends(get_db)):
    rate_limit(f"reco-refresh:{user['id']}", 12, 3600)  # one full batch-score per call: keep it rare
    st = R.refresh(db, user["id"])
    db.commit()
    return {"computed_at": st.computed_at, "candidates": st.candidates, "cold_start": st.cold_start}
