import os
import secrets
import smtplib

import httpx
from datetime import datetime, timedelta
from email.message import EmailMessage
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db import get_db
from .auth import (CSRF_COOKIE, SESSION_COOKIE, cookie_options, current_user, digest,
                   email_address, hash_password, new_session, public_user, rate_limit, request_limit,
                   revoke_sessions, verify_password)
from .models import CsrfToken, PasswordReset, Profile, User, UserActivity, UserSession

router = APIRouter(prefix="/api", tags=["accounts"])


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)


class Register(Input):
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(max_length=254)
    password: str = Field(min_length=6, max_length=128)


class Login(Input):
    email: str = Field(max_length=254)
    password: str = Field(max_length=128)


@router.get("/auth/csrf")
def csrf(request: Request, response: Response, db: Session = Depends(get_db)):
    rate_limit(f"csrf:{request.client.host}", 120, 60)
    value = secrets.token_urlsafe(32)
    if getattr(request.state, "user", None):
        session = db.get(UserSession, request.state.session_hash)
        old = request.cookies.get(CSRF_COOKIE, "")
        if old and digest(old) == session.csrf_hash:
            value = old
        else:
            session.csrf_hash = digest(value)
    else:
        db.add(CsrfToken(token_hash=digest(value), expires_at=datetime.utcnow() + timedelta(minutes=30)))
    db.commit()
    response.set_cookie(CSRF_COOKIE, value, httponly=False, max_age=604800, **cookie_options())
    return {"csrf_token": value}


@router.post("/auth/register", status_code=201)
def register(body: Register, request: Request, db: Session = Depends(get_db)):
    request_limit(request, "register", 8)
    email = email_address(body.email)
    if not body.name.strip():
        raise HTTPException(422, "Vui lòng nhập tên")
    encoded = hash_password(body.password)
    user = User(email=email, name=body.name.strip(), password_hash=encoded, role="MKT", status="active")
    try:
        db.add(user)
        db.flush()
        db.add(Profile(user_id=user.id))
        db.add(UserActivity(user_id=user.id, kind="registered"))
        db.commit()
    except IntegrityError:
        db.rollback()
    return {"message": "Đăng ký thành công. Bạn có thể đăng nhập ngay."}


@router.post("/auth/login")
def login(body: Login, request: Request, response: Response, db: Session = Depends(get_db)):
    request_limit(request, "login", 30)
    email = email_address(body.email)
    rate_limit(f"login-email:{email}", 10)
    user = db.scalar(select(User).where(User.email == email))
    if not verify_password(user.password_hash if user else None, body.password) or not user:
        raise HTTPException(401, "Email hoặc mật khẩu không đúng")
    if user.status != "active":
        raise HTTPException(403, "Tài khoản đã bị khóa hoặc chưa được kích hoạt")
    old = request.cookies.get(SESSION_COOKIE)
    if old:
        db.execute(delete(UserSession).where(UserSession.token_hash == digest(old)))
    new_session(db, user, response)
    db.add(UserActivity(user_id=user.id, kind="login"))
    db.commit()
    return {"user": public_user(user)}


@router.get("/auth/me")
def me(user=Depends(current_user), db: Session = Depends(get_db)):
    p = db.get(Profile, user["id"])
    return {"user": user, "profile": {"bio": p.bio if p else "", "team": p.team if p else ""}}


@router.post("/auth/logout")
def logout(request: Request, response: Response, user=Depends(current_user), db: Session = Depends(get_db)):
    db.execute(delete(UserSession).where(UserSession.token_hash == request.state.session_hash))
    db.add(UserActivity(user_id=user["id"], kind="logout"))
    db.commit()
    for key in (SESSION_COOKIE, CSRF_COOKIE):
        response.delete_cookie(key, **cookie_options())
    return {"ok": True}


class ProfileInput(Input):
    name: str = Field(min_length=1, max_length=100)
    bio: str = Field(default="", max_length=1000)
    team: Literal["", "mess", "ladi"] = ""


@router.patch("/auth/profile")
def profile(body: ProfileInput, user=Depends(current_user), db: Session = Depends(get_db)):
    if not body.name.strip():
        raise HTTPException(422, "Vui lòng nhập tên")
    db.get(User, user["id"]).name = body.name.strip()
    p = db.get(Profile, user["id"])
    if not p:
        p = Profile(user_id=user["id"])
        db.add(p)
    p.bio, p.team = body.bio, body.team
    db.commit()
    return {"ok": True}


class PasswordChange(Input):
    current_password: str = Field(max_length=128)
    new_password: str = Field(min_length=6, max_length=128)


@router.post("/auth/password")
def password(body: PasswordChange, response: Response, user=Depends(current_user), db: Session = Depends(get_db)):
    rate_limit(f"password:{user['id']}", 8)
    u = db.get(User, user["id"])
    if not verify_password(u.password_hash, body.current_password):
        raise HTTPException(400, "Mật khẩu hiện tại không đúng")
    u.password_hash = hash_password(body.new_password)
    revoke_sessions(db, u.id)
    db.execute(delete(PasswordReset).where(PasswordReset.user_id == u.id))
    new_session(db, u, response)
    db.add(UserActivity(user_id=u.id, kind="password_changed"))
    db.commit()
    return {"ok": True}


OTP_MINUTES = 10
OTP_TRIES = 5  # wrong codes allowed per email per window before the code is locked


def otp_hash(email, otp):
    return digest(f"{email}:{otp}")  # bound to the email: the same 6 digits never match another account


def issue_reset(db, user):
    """New single-use 6-digit code for this account (older codes are dropped). Returns the plain code once."""
    otp = f"{secrets.randbelow(10**6):06d}"
    db.execute(delete(PasswordReset).where(PasswordReset.user_id == user.id))
    db.add(PasswordReset(user_id=user.id, token_hash=otp_hash(user.email, otp),
                         expires_at=datetime.utcnow() + timedelta(minutes=OTP_MINUTES)))
    return otp


def send_otp(user, otp):
    """EmailJS (REST) when EMAILJS_* is set, else SMTP when SMTP_HOST is set. Returns False when nothing is configured."""
    if os.getenv("EMAILJS_SERVICE_ID"):
        r = httpx.post("https://api.emailjs.com/api/v1.0/email/send", timeout=15, json={
            "service_id": os.environ["EMAILJS_SERVICE_ID"], "template_id": os.environ["EMAILJS_TEMPLATE_ID"],
            "user_id": os.environ["EMAILJS_PUBLIC_KEY"], "accessToken": os.environ["EMAILJS_PRIVATE_KEY"],
            "template_params": {"to_email": user.email, "to_name": user.name, "otp_code": otp, "expires_minutes": OTP_MINUTES,
                                "email": os.getenv("EMAILJS_REPLY_TO") or user.email}})
        if r.status_code != 200:  # keep EmailJS's own reason (e.g. 403 "non-browser disabled") for the activity log
            raise httpx.HTTPError(f"EmailJS {r.status_code}: {r.text[:200]}")
        return True
    if os.getenv("SMTP_HOST"):
        message = EmailMessage()
        message["Subject"], message["From"], message["To"] = "[ToolSpy] Mã OTP đặt lại mật khẩu", os.getenv("SMTP_FROM", "toolspy@localhost"), user.email
        message.set_content(f"Xin chào {user.name},\n\nMã OTP đặt lại mật khẩu của bạn: {otp}\nMã có hiệu lực trong {OTP_MINUTES} phút và chỉ dùng một lần.")
        with smtplib.SMTP(os.environ["SMTP_HOST"], int(os.getenv("SMTP_PORT", "587")), timeout=10) as smtp:
            smtp.starttls()
            if os.getenv("SMTP_USER"):
                smtp.login(os.environ["SMTP_USER"], os.getenv("SMTP_PASSWORD", ""))
            smtp.send_message(message)
        return True
    return False


class Forgot(Input):
    email: str = Field(max_length=254)


@router.post("/auth/forgot-password")
def forgot(body: Forgot, request: Request, db: Session = Depends(get_db)):
    request_limit(request, "forgot", 8)
    email = email_address(body.email)
    rate_limit(f"forgot-email:{email}", 3, 3600)
    u = db.scalar(select(User).where(User.email == email))
    if u:
        db.add(UserActivity(user_id=u.id, kind="password_reset_requested"))
        configured = bool(os.getenv("EMAILJS_SERVICE_ID") or os.getenv("SMTP_HOST"))
        otp = issue_reset(db, u) if configured else None
        db.commit()
        if otp:
            try:
                send_otp(u, otp)
            except (OSError, smtplib.SMTPException, httpx.HTTPError) as e:
                # Generic response avoids account enumeration; the reason goes to the activity log (never to the caller).
                db.add(UserActivity(user_id=u.id, kind="password_reset_delivery_failed", data={"error": str(e)[:300]}))
                db.commit()
    return {"message": "Nếu tài khoản tồn tại, mã OTP đã được gửi tới email (hiệu lực %d phút). Chưa nhận được mail thì kiểm tra thư rác hoặc liên hệ bộ phận vận hành." % OTP_MINUTES}


class Reset(Input):
    email: str = Field(max_length=254)
    otp: str = Field(pattern=r"^\d{6}$")
    password: str = Field(min_length=6, max_length=128)


@router.post("/auth/reset-password")
def reset(body: Reset, request: Request, db: Session = Depends(get_db)):
    request_limit(request, "reset", 10)
    email = email_address(body.email)
    rate_limit(f"reset-otp:{email}", OTP_TRIES, OTP_MINUTES * 60)  # 6 digits are guessable: cap attempts per account
    encoded = hash_password(body.password)
    u = db.scalar(select(User).where(User.email == email))
    reset = db.scalar(select(PasswordReset).where(PasswordReset.user_id == u.id, PasswordReset.token_hash == otp_hash(email, body.otp))
                      .with_for_update()) if u else None
    if not reset or reset.used_at or reset.expires_at <= datetime.utcnow():
        raise HTTPException(400, "Mã OTP không đúng hoặc đã hết hạn")
    u.password_hash, reset.used_at = encoded, datetime.utcnow()
    revoke_sessions(db, u.id)
    db.add(UserActivity(user_id=u.id, kind="password_reset"))
    db.commit()
    return {"ok": True}
