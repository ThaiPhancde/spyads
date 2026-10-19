"""Opaque DB-backed sessions; credentials never travel through localStorage or frontend bundles."""
import hashlib
import hmac
import os
import re
import secrets
import threading
from datetime import datetime, timedelta
from urllib.parse import urlsplit

from argon2 import PasswordHasher, Type
from argon2.exceptions import VerificationError, InvalidHashError
from fastapi import Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import delete, select
from starlette.concurrency import run_in_threadpool

from ..db import SessionLocal, get_db
from .models import AuthRateLimit, CsrfToken, User, UserSession

SESSION_COOKIE = "toolspy_session"
CSRF_COOKIE = "toolspy_csrf"
HASHER = PasswordHasher(time_cost=3, memory_cost=32768, parallelism=1, type=Type.ID)
HASH_SLOTS = threading.BoundedSemaphore(2)
DUMMY_HASH = HASHER.hash(secrets.token_urlsafe(32))
PUBLIC = {"/api/health", "/api/auth/csrf", "/api/auth/register", "/api/auth/login",
          "/api/auth/forgot-password", "/api/auth/reset-password"}
MACHINE = ("/api/ingest/", "/api/eu-market/allegro/callback/", "/api/agent/v1/")
# System areas (connectors / crawler config, storage, agent, EU sync, collector internals): internal service token only.
# There is no ADMIN account: every person is an MKT and never gets these, not even read access.
SYSTEM = ("/api/admin/", "/api/storage/", "/api/agent/", "/api/eu-market/", "/api/collector/")


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def email_address(value):
    value = value.strip().casefold()
    if len(value) > 254 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
        raise HTTPException(422, "Email không hợp lệ")
    return value


def hash_password(value):
    if not 6 <= len(value) <= 128:
        raise HTTPException(422, "Mật khẩu cần từ 6 đến 128 ký tự")
    if not HASH_SLOTS.acquire(timeout=2):
        raise HTTPException(429, "Hệ thống bận, vui lòng thử lại")
    try:
        return HASHER.hash(value)
    finally:
        HASH_SLOTS.release()


def verify_password(encoded, value):
    if len(value) > 128:
        return False
    if not HASH_SLOTS.acquire(timeout=2):
        raise HTTPException(429, "Hệ thống bận, vui lòng thử lại")
    try:
        return HASHER.verify(encoded or DUMMY_HASH, value)
    except (VerificationError, InvalidHashError):
        return False
    finally:
        HASH_SLOTS.release()


def rate_limit(key, limit=10, seconds=900):
    """Atomic fixed windows across processes. Store only hashed IP/email keys."""
    now = datetime.utcnow()
    start = int(now.timestamp()) // seconds
    key = digest(f"{key}:{start}")
    with SessionLocal.begin() as db:
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert
        insert = sqlite_insert if db.bind.dialect.name == "sqlite" else pg_insert
        stmt = insert(AuthRateLimit).values(key=key, count=1, expires_at=now + timedelta(seconds=seconds * 2))
        count = db.scalar(stmt.on_conflict_do_update(index_elements=["key"], set_={"count": AuthRateLimit.count + 1})
                          .returning(AuthRateLimit.count))
    if count > limit:
        raise HTTPException(429, "Quá nhiều yêu cầu. Vui lòng thử lại sau.", headers={"Retry-After": str(seconds)})


def request_limit(request, action, limit=10):
    # Forwarded headers are deliberately not trusted; configure a trusted proxy in uvicorn for client IPs.
    rate_limit(f"{action}:{request.client.host if request.client else 'unknown'}", limit)


def cookie_options():
    app_url = os.getenv("APP_URL", "http://localhost:3000")
    local = urlsplit(app_url).hostname in ("localhost", "127.0.0.1", "::1")
    secure = os.getenv("AUTH_COOKIE_SECURE", "0" if local and app_url.startswith("http:") else "1") != "0"
    return {"secure": secure, "samesite": "lax", "path": "/"}


def public_user(user):
    return {"id": user.id, "name": user.name, "email": user.email, "role": user.role, "status": user.status}


def current_user(request: Request):
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(401, "Vui lòng đăng nhập")
    return user


def new_session(db, user, response):
    token, csrf = secrets.token_urlsafe(48), secrets.token_urlsafe(32)
    seconds = max(3600, min(int(os.getenv("AUTH_SESSION_HOURS", "168")), 720) * 3600)
    db.add(UserSession(token_hash=digest(token), csrf_hash=digest(csrf), user_id=user.id,
                       expires_at=datetime.utcnow() + timedelta(seconds=seconds)))
    response.set_cookie(SESSION_COOKIE, token, httponly=True, max_age=seconds, **cookie_options())
    response.set_cookie(CSRF_COOKIE, csrf, httponly=False, max_age=seconds, **cookie_options())


def revoke_sessions(db, user_id):
    db.execute(delete(UserSession).where(UserSession.user_id == user_id))


def mkt_write(path):
    return (path.startswith(("/api/auth/", "/api/workspace/", "/api/competitors/"))
            or re.fullmatch(r"/api/clusters/candidates/\d+/(accept|reject)", path)
            or re.fullmatch(r"/api/clusters/events/\d+/undo", path)
            or re.fullmatch(r"/api/allocations/(claim|\d+/(release|status|results))", path)
            or path == "/api/behavior/events"
            or path in ("/api/preferences", "/api/preferences/reset", "/api/preferences/refresh")
            or path == "/api/recommendations/refresh"
            or re.fullmatch(r"/api/library/products/\d+/(save|favorite|start-test)", path)
            or path == "/api/search/live" or re.fullmatch(r"/api/search/jobs/\d+/more", path)
            or re.fullmatch(r"/api/products/\d+/vote", path)
            or re.fullmatch(r"/api/creatives/\d+/retry", path)
            or re.fullmatch(r"/api/search/images/results/\d+/(verify|save)", path)
            or path in ("/api/search/images/upload", "/api/search/images", "/api/search/images/by-url", "/api/search/images/from-product",
                        "/api/search/images/live", "/api/products/visual-match", "/api/products/compare-prices-by-image", "/api/prices/enrich", "/api/prices/lookup",
                        "/api/engagement/enrich", "/api/engagement/lookup"))


def authenticate_request(request):
    path = request.url.path.rstrip("/")
    if path.startswith(MACHINE) or path == "/api/imports/allegro":
        return  # these routes enforce their own integration credentials
    write = request.method not in ("GET", "HEAD", "OPTIONS")
    if request.method == "OPTIONS":
        return
    # Retain explicit server-to-server legacy administration, never accepted for private account data.
    service = request.headers.get("x-admin-token", "")
    want = os.getenv("ADMIN_TOKEN", "")
    private = path.startswith(("/api/auth/", "/api/admin/", "/api/workspace/", "/api/competitors"))
    if service and want and not private and hmac.compare_digest(service, want):
        request.state.service_admin = True
        return
    with SessionLocal() as db:
        session = db.get(UserSession, digest(request.cookies.get(SESSION_COOKIE, "")))
        user = db.get(User, session.user_id) if session and session.expires_at > datetime.utcnow() else None
        if user and user.status != "active":
            user = None
        if user:
            request.state.user = public_user(user)
            request.state.session_hash = session.token_hash
        if path not in PUBLIC and not user:
            raise HTTPException(401, "Vui lòng đăng nhập")
        if write:
            origin = request.headers.get("origin")
            origins = {o.strip().rstrip("/") for o in os.getenv("WEB_ORIGIN", "http://localhost:3000,http://127.0.0.1:3000").split(",")}
            origins.add(os.getenv("APP_URL", "http://localhost:3000").rstrip("/"))
            if origin and origin.rstrip("/") not in origins:
                raise HTTPException(403, "Origin không được phép")
            cookie, header = request.cookies.get(CSRF_COOKIE, ""), request.headers.get("x-csrf-token", "")
            if not cookie or not header or not hmac.compare_digest(cookie, header):
                raise HTTPException(403, "CSRF token không hợp lệ; tải lại trang")
            if user:
                valid = hmac.compare_digest(session.csrf_hash, digest(header))
            else:
                token = db.get(CsrfToken, digest(header))
                valid = token is not None and token.expires_at > datetime.utcnow()
            if not valid:
                raise HTTPException(403, "CSRF token hết hạn; tải lại trang")
        if user:  # every account is an MKT: self-service only; system work runs with the internal service token
            if path.startswith(SYSTEM):
                raise HTTPException(403, "Khu vực hệ thống chỉ dành cho dịch vụ nội bộ")
            if write and not mkt_write(path):
                raise HTTPException(403, "Thao tác hệ thống chỉ chạy bằng dịch vụ nội bộ")


class SessionGuard:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"].startswith("/api/"):
            request = Request(scope)
            try:
                await run_in_threadpool(authenticate_request, request)
            except HTTPException as exc:
                return await JSONResponse({"detail": exc.detail}, status_code=exc.status_code,
                                          headers=exc.headers)(scope, receive, send)
            async def no_cache(message):
                if message["type"] == "http.response.start":
                    headers = list(message.get("headers", []))
                    headers.append((b"cache-control", b"private, no-store"))
                    message["headers"] = headers
                await send(message)
            return await self.app(scope, receive, no_cache)
        await self.app(scope, receive, send)
