"""Market Intelligence OS backend.

Loads the repo-root `.env` (KEY=VALUE lines) on import, without overriding variables already set in the
environment — so `uvicorn app.main:app`, `python -m app.r2_sync` and start.ps1 all see the same config.
"""
import os as _os
from pathlib import Path as _Path


def _load_dotenv():
    up = _Path(__file__).resolve().parents  # in Docker (/app/app/__init__.py) there is no 4th parent: env comes from compose
    for p in [up[i] / ".env" for i in (3, 1) if i < len(up)]:
        if not p.is_file():
            continue
        for raw in p.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            v = v.split(" #", 1)[0].strip().strip('"').strip("'")
            if v and k.strip() not in _os.environ:
                _os.environ[k.strip()] = v


_load_dotenv()
