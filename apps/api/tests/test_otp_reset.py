"""Password reset by 6-digit OTP, delivered through EmailJS (REST) or SMTP. The sender is faked: no mail leaves the machine."""
from datetime import datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.workspace import accounts
from app.workspace.models import PasswordReset, User, UserActivity
from test_workspace import PASSWORD, client, create_user, isolated, write  # noqa: F401 — isolated: autouse fresh DB


@pytest.fixture(autouse=True)
def mail(monkeypatch):
    for k in ("SMTP_HOST", "EMAILJS_SERVICE_ID", "EMAILJS_TEMPLATE_ID", "EMAILJS_PUBLIC_KEY", "EMAILJS_PRIVATE_KEY", "EMAILJS_REPLY_TO"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "0")
    sent = []

    class Resp:
        status_code = 200

        def raise_for_status(self):
            return None
    monkeypatch.setattr(accounts.httpx, "post", lambda url, **kw: (sent.append((url, kw["json"])), Resp())[1])
    return sent


def emailjs(monkeypatch):
    monkeypatch.setenv("EMAILJS_SERVICE_ID", "service_test")
    monkeypatch.setenv("EMAILJS_TEMPLATE_ID", "template_test")
    monkeypatch.setenv("EMAILJS_PUBLIC_KEY", "pub")
    monkeypatch.setenv("EMAILJS_PRIVATE_KEY", "priv")


def test_forgot_sends_otp_through_emailjs_with_the_template_variables(mail, monkeypatch):
    emailjs(monkeypatch)
    create_user()
    r = write(client(), "POST", "/auth/forgot-password", {"email": "mkt-a@example.test"})
    assert r.status_code == 200 and "10 phút" in r.json()["message"]
    (url, body), = mail
    assert url == "https://api.emailjs.com/api/v1.0/email/send"
    assert (body["service_id"], body["template_id"], body["user_id"], body["accessToken"]) == ("service_test", "template_test", "pub", "priv")
    p = body["template_params"]
    assert set(p) == {"to_email", "to_name", "otp_code", "expires_minutes", "email"}  # exactly what the template uses
    assert p["to_email"] == "mkt-a@example.test" and p["expires_minutes"] == 10 and len(p["otp_code"]) == 6 and p["otp_code"].isdigit()


def test_full_flow_otp_resets_password_once_and_revokes_sessions(mail, monkeypatch):
    emailjs(monkeypatch)
    create_user()
    old = client("mkt-a@example.test")
    write(client(), "POST", "/auth/forgot-password", {"email": "mkt-a@example.test"})
    otp = mail[0][1]["template_params"]["otp_code"]
    anon = client()
    body = {"email": "mkt-a@example.test", "otp": otp, "password": "A brand new password 1"}
    assert write(anon, "POST", "/auth/reset-password", body).status_code == 200
    assert write(anon, "POST", "/auth/reset-password", body).status_code == 400
    assert old.get("/api/auth/me").status_code == 401
    assert write(client(), "POST", "/auth/login", {"email": "mkt-a@example.test", "password": "A brand new password 1"}).status_code == 200
    assert write(client(), "POST", "/auth/login", {"email": "mkt-a@example.test", "password": PASSWORD}).status_code == 401
    with SessionLocal() as db:  # stored hashed and bound to the email: the plain code is nowhere in the table
        row = db.scalar(select(PasswordReset))
        assert otp not in row.token_hash and row.used_at is not None


def test_wrong_expired_or_foreign_codes_are_refused_and_guessing_is_capped(mail, monkeypatch):
    emailjs(monkeypatch)
    create_user(); create_user("mkt-b@example.test")
    write(client(), "POST", "/auth/forgot-password", {"email": "mkt-a@example.test"})
    otp = mail[0][1]["template_params"]["otp_code"]
    wrong = "000000" if otp != "000000" else "111111"
    anon = client()
    mine = {"email": "mkt-a@example.test", "otp": otp, "password": PASSWORD}
    assert write(anon, "POST", "/auth/reset-password", {**mine, "otp": wrong}).status_code == 400
    assert write(anon, "POST", "/auth/reset-password", {**mine, "email": "mkt-b@example.test"}).status_code == 400  # same code, other account
    assert write(anon, "POST", "/auth/reset-password", {**mine, "otp": "12ab56"}).status_code == 422
    with SessionLocal.begin() as db:
        db.scalar(select(PasswordReset)).expires_at = datetime.utcnow() - timedelta(seconds=1)
    assert write(anon, "POST", "/auth/reset-password", mine).status_code == 400  # expired
    codes = [write(client(), "POST", "/auth/reset-password", {**mine, "otp": f"{i:06d}"}).status_code for i in range(8)]
    assert codes[-1] == 429  # attempts per account are capped, so 6 digits cannot be brute-forced


def test_unknown_email_looks_the_same_and_nothing_is_sent(mail, monkeypatch):
    emailjs(monkeypatch)
    create_user()
    known = write(client(), "POST", "/auth/forgot-password", {"email": "mkt-a@example.test"}).json()
    unknown = write(client(), "POST", "/auth/forgot-password", {"email": "nobody@example.test"}).json()
    assert known == unknown and len(mail) == 1


def test_provider_failure_is_logged_but_not_revealed(mail, monkeypatch):
    emailjs(monkeypatch)
    create_user()

    class Denied:
        status_code = 403
        text = "API access from non-browser environments is currently disabled."
    monkeypatch.setattr(accounts.httpx, "post", lambda url, **kw: Denied())
    r = write(client(), "POST", "/auth/forgot-password", {"email": "mkt-a@example.test"})
    assert r.status_code == 200 and "403" not in r.text  # the caller never learns why
    with SessionLocal() as db:
        row = db.scalar(select(UserActivity).where(UserActivity.kind == "password_reset_delivery_failed"))
        assert row is not None and "403" in row.data["error"] and "non-browser" in row.data["error"]


def test_nothing_configured_sends_nothing_and_cli_issues_a_code(mail, monkeypatch):
    uid = create_user()
    assert write(client(), "POST", "/auth/forgot-password", {"email": "mkt-a@example.test"}).status_code == 200
    assert mail == []
    from app.workspace import cli
    import io, contextlib
    monkeypatch.setattr("sys.argv", ["cli", "reset-password", "mkt-a@example.test"])
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        cli.main()
    otp = out.getvalue().strip().splitlines()[-1]
    assert write(client(), "POST", "/auth/reset-password", {"email": "mkt-a@example.test", "otp": otp, "password": PASSWORD}).status_code == 200
