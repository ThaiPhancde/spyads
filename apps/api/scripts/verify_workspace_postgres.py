"""Run workspace regressions against a new disposable PostgreSQL database, then remove that test database."""
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.db import engine
from sqlalchemy import create_engine, text

assert engine.dialect.name == "postgresql"
name = "toolspy_test_" + uuid.uuid4().hex
assert re.fullmatch(r"toolspy_test_[a-f0-9]{32}", name)
admin = create_engine(engine.url, isolation_level="AUTOCOMMIT")
with admin.connect() as c:
    c.execute(text(f'CREATE DATABASE "{name}"'))
try:
    env = dict(os.environ, TOOLSPY_TEST_DATABASE_URL=engine.url.set(database=name).render_as_string(hide_password=False))
    result = subprocess.run([sys.executable, "-m", "pytest", "tests/test_workspace.py", "-q", "--disable-warnings"], env=env)
finally:
    with admin.connect() as c:
        c.execute(text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=:name AND pid<>pg_backend_pid()"), {"name": name})
        c.execute(text(f'DROP DATABASE "{name}"'))
    admin.dispose()
raise SystemExit(result.returncode)
