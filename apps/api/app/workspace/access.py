from fastapi import HTTPException
from .models import ResourceAccess


def grant(db, resource, resource_id):
    uid = db.info.get("user_id")
    if not uid:
        return
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
    insert = sqlite_insert if db.bind.dialect.name == "sqlite" else pg_insert
    db.execute(insert(ResourceAccess).values(user_id=uid, resource=resource, resource_id=resource_id).on_conflict_do_nothing())
    db.commit()


def require_access(db, resource, resource_id):
    uid = db.info.get("user_id")
    if uid and not db.get(ResourceAccess, (uid, resource, resource_id)):
        raise HTTPException(404, "Không tìm thấy dữ liệu trong tài khoản của bạn")
