"""Phase 6 API: similar products / ads, based-on-history and more-from-followed (app/similar.py). Read-only."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from .. import similar as S
from ..db import get_db
from ..workspace.auth import current_user

router = APIRouter(prefix="/api/similar", tags=["similar"], dependencies=[Depends(current_user)])


@router.get("/products/{pid}")
def similar_products(pid: int, limit: int = Query(20, ge=1, le=100), exclusive: bool = False,
                     user=Depends(current_user), db: Session = Depends(get_db)):
    """`exclusive=true` keeps only products no other MKT owns (opportunity view); otherwise each row says its allocation."""
    out = S.similar_products(db, pid, user["id"], limit, exclusive)
    if out is None:
        raise HTTPException(404, "Không tìm thấy sản phẩm")
    return out


@router.get("/ads/{aid}")
def similar_ads(aid: int, limit: int = Query(20, ge=1, le=100), user=Depends(current_user), db: Session = Depends(get_db)):
    out = S.similar_ads(db, aid, limit)
    if out is None:
        raise HTTPException(404, "Không tìm thấy quảng cáo")
    return out


@router.get("/for-me")
def for_me(limit: int = Query(30, ge=1, le=100), user=Depends(current_user), db: Session = Depends(get_db)):
    return S.for_me(db, user["id"], limit)


@router.get("/followed")
def followed(limit: int = Query(30, ge=1, le=100), user=Depends(current_user), db: Session = Depends(get_db)):
    return S.from_followed(db, user["id"], limit)
