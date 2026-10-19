"""Internal operations CLI (run on the server from apps/api) — there is no ADMIN account in the web app:

    python -m app.workspace.cli create-user EMAIL      MKT account, active at once (same as self-registration)
    python -m app.workspace.cli reset-password EMAIL   single-use OTP when email sending is not configured
    python -m app.workspace.cli lock EMAIL | unlock EMAIL   suspend / restore an account (sessions revoked on lock)
"""
import argparse
import getpass

from sqlalchemy import select

from ..db import SessionLocal
from .accounts import issue_reset
from .auth import email_address, hash_password, revoke_sessions
from .migrate import migrate
from .models import Profile, User, UserActivity


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=["create-user", "reset-password", "lock", "unlock"])
    p.add_argument("email", nargs="?")
    args = p.parse_args()
    migrate()
    email = email_address(args.email or input("Email: "))
    with SessionLocal.begin() as db:
        user = db.scalar(select(User).where(User.email == email))
        if args.command == "create-user":
            if user:
                raise SystemExit("Email already exists")
            name = input("Name: ").strip()
            password = getpass.getpass("Password (6-128 characters): ")
            if not name or password != getpass.getpass("Confirm password: "):
                raise SystemExit("Name missing or passwords do not match")
            user = User(email=email, name=name, password_hash=hash_password(password), role="MKT", status="active")
            db.add(user)
            db.flush()
            db.add(Profile(user_id=user.id))
            db.add(UserActivity(user_id=user.id, kind="registered", data={"by": "cli"}))
            print("MKT account created and active.")
            return
        if not user:
            raise SystemExit("Account not found")
        if args.command == "reset-password":
            token = issue_reset(db, user)
            revoke_sessions(db, user.id)
            db.add(UserActivity(user_id=user.id, kind="reset_link_issued_by_cli"))
            print("Single-use OTP, expires in 10 minutes; deliver to the verified account owner (reset page: email + OTP + new password):")
            print(token)
        else:
            user.status = "locked" if args.command == "lock" else "active"
            if args.command == "lock":
                revoke_sessions(db, user.id)
            db.add(UserActivity(user_id=user.id, kind=f"account_{user.status}", data={"by": "cli"}))
            print(f"Account {user.status}.")


if __name__ == "__main__":
    main()
