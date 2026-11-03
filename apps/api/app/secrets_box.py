"""Connector credentials at rest (P5): the `secret: True` config fields (API tokens, proxy user:pass, pasted session headers)
are sealed with Fernet (AES-128-CBC + HMAC-SHA256, `cryptography`) when CREDENTIALS_KEY is set, and opened only when a
connector is built (ConnectorFactory.get) — never in API responses (masked) or logs.

No key set: values stay as they were (nothing breaks; the KPI `credentials_unencrypted` says how many are exposed).
    python -m app.secrets_box --generate-key   → a key for CREDENTIALS_KEY (keep it outside the database and git)
    python -m app.secrets_box --seal-existing  → seal secrets saved before the key existed (idempotent)
Losing the key makes sealed secrets unreadable: re-enter them in Data & Connectors.
"""
from __future__ import annotations

import os

PREFIX = "enc:v1:"
MASK = "••••"


def _fernet():
    key = os.getenv("CREDENTIALS_KEY", "").strip()
    if not key:
        return None
    from cryptography.fernet import Fernet

    return Fernet(key.encode())


def is_sealed(value) -> bool:
    return isinstance(value, str) and value.startswith(PREFIX)


def seal(value):
    """A secret string → "enc:v1:<token>" (no key: unchanged). Empty, masked and already sealed values pass through."""
    if not isinstance(value, str) or not value or value == MASK or is_sealed(value):
        return value
    f = _fernet()
    return PREFIX + f.encrypt(value.encode()).decode() if f else value


def unseal(value):
    if not is_sealed(value):
        return value
    f = _fernet()
    if f is None:
        from .collector.base import NotConfigured

        raise NotConfigured("Secret đã mã hoá nhưng thiếu CREDENTIALS_KEY — đặt lại khoá hoặc nhập lại secret trong Data & Connectors")
    from cryptography.fernet import InvalidToken

    try:
        return f.decrypt(value[len(PREFIX):].encode()).decode()
    except InvalidToken as e:
        from .collector.base import NotConfigured

        raise NotConfigured("CREDENTIALS_KEY không mở được secret đã lưu (khoá đã đổi?) — nhập lại secret") from e


def secret_keys(adapter: str) -> set[str]:
    from .collector.factory import ConnectorFactory

    cls = ConnectorFactory.connectors.get(adapter)
    return {f["key"] for f in (cls.config_fields if cls else []) if f.get("secret")}


def seal_config(adapter: str, cfg: dict | None) -> dict:
    keys = secret_keys(adapter)
    return {k: seal(v) if k in keys else v for k, v in (cfg or {}).items()}


def unseal_config(cfg):
    """Every sealed string anywhere in the config (nested configs too, e.g. a 1688 connector's Apify fallback)."""
    if isinstance(cfg, dict):
        return {k: unseal_config(v) for k, v in cfg.items()}
    if isinstance(cfg, list):
        return [unseal_config(v) for v in cfg]
    return unseal(cfg)


def exposed(adapter: str, cfg: dict | None) -> int:
    """Secret fields holding a plaintext value."""
    return sum(1 for k in secret_keys(adapter) if (cfg or {}).get(k) and not is_sealed((cfg or {})[k]))


def seal_existing() -> dict:
    from .db import SessionLocal
    from .models import Connector

    if _fernet() is None:
        raise SystemExit("CREDENTIALS_KEY chưa đặt")
    out = {"connectors": 0, "fields": 0}
    with SessionLocal() as db:
        for c in db.query(Connector).all():
            n = exposed(c.adapter or "", c.config)
            if n:
                c.config = seal_config(c.adapter, c.config)
                out["connectors"] += 1
                out["fields"] += n
        db.commit()
    return out


if __name__ == "__main__":
    import sys

    if "--generate-key" in sys.argv:
        from cryptography.fernet import Fernet

        print(Fernet.generate_key().decode())
    elif "--seal-existing" in sys.argv:
        print(seal_existing())
    else:
        print(__doc__)
