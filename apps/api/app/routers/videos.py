"""Video identity API (design §2.2): one video = creatives sharing a cover-frame key (app/videos.py). Read-only."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import videos
from ..db import get_db
from ..workspace.auth import current_user

router = APIRouter(prefix="/api/videos", tags=["videos"], dependencies=[Depends(current_user)])


@router.get("/{key}")
def video(key: str, db: Session = Depends(get_db)):
    """Every ad and page of one video, active or not, pages in order of arrival."""
    out = videos.facts_for(db, [key.lower()[:16]], with_ads=True).get(key.lower()[:16])
    if out is None:
        raise HTTPException(404, "Không tìm thấy video")
    return out
