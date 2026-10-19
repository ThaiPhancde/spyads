"""Phase 4 API: read / edit / reset the MKT's own preference profile (app/preferences.py). No AI calls."""
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import preferences as P
from ..db import get_db
from ..workspace.auth import current_user

router = APIRouter(prefix="/api/preferences", tags=["preferences"], dependencies=[Depends(current_user)])


class ManualIn(BaseModel):
    """Only the fields sent are changed; send null to clear one field, or use /reset for everything."""
    priority_markets: list[str] | None = Field(None, max_length=len(P.MARKETS))
    categories: list[str] | None = Field(None, max_length=30)
    excluded_categories: list[str] | None = Field(None, max_length=30)
    countries: list[str] | None = Field(None, max_length=40)
    languages: list[str] | None = Field(None, max_length=20)
    price_min: float | None = Field(None, ge=0)
    price_max: float | None = Field(None, ge=0)
    currency: str | None = Field(None, pattern=r"^[A-Za-z]{3}$")


class ResetIn(BaseModel):
    scope: Literal["manual", "learned", "all"] = "all"


def target(user, user_id):
    if user_id is not None and user_id != user["id"]:  # another account's data is never readable
        raise HTTPException(404, "Không tìm thấy hồ sơ trong tài khoản của bạn")
    return user["id"]


@router.get("")
def read(user_id: int | None = None, user=Depends(current_user), db: Session = Depends(get_db)):
    return P.view(db, target(user, user_id))


@router.put("")
def update(body: ManualIn, user=Depends(current_user), db: Session = Depends(get_db)):
    sent = body.model_dump(exclude_unset=True)
    p = db.get(P.PreferenceProfile, user["id"]) or P.PreferenceProfile(user_id=user["id"], learned={}, manual={})
    manual = {**(p.manual or {}), **P.validate({k: v for k, v in sent.items() if v is not None})}
    for k, v in sent.items():
        if v is None:
            manual.pop(k, None)
    lo, hi = manual.get("price_min"), manual.get("price_max")
    if lo is not None and hi is not None and lo > hi:
        raise HTTPException(422, "Giá tối thiểu lớn hơn giá tối đa")
    p.manual, p.updated_at = manual, datetime.utcnow()
    db.add(p)
    db.commit()
    return P.view(db, user["id"])


@router.post("/reset")
def reset(body: ResetIn, user=Depends(current_user), db: Session = Depends(get_db)):
    p = db.get(P.PreferenceProfile, user["id"]) or P.PreferenceProfile(user_id=user["id"], learned={}, manual={})
    if body.scope in ("manual", "all"):
        p.manual = {}
    if body.scope in ("learned", "all"):
        p.learned_reset_at = datetime.utcnow()  # history stays in Phase 3; the profile just stops learning from it
        db.add(p)
        db.flush()
        P.refresh(db, user["id"])
    p.updated_at = datetime.utcnow()
    db.add(p)
    db.commit()
    return P.view(db, user["id"])


@router.post("/refresh")
def refresh(user=Depends(current_user), db: Session = Depends(get_db)):
    P.refresh(db, user["id"])
    db.commit()
    return P.view(db, user["id"])
