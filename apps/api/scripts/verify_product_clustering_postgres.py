"""Verify Phase 1 against an isolated PostgreSQL DB; never copy or modify production data."""
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.db import engine
from sqlalchemy import create_engine, text


def main():
    assert engine.dialect.name == "postgresql"
    name = "toolspy_test_cluster_" + uuid.uuid4().hex
    assert re.fullmatch(r"toolspy_test_cluster_[a-f0-9]{32}", name)
    admin = create_engine(engine.url, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        env = dict(os.environ, TOOLSPY_TEST_DATABASE_URL=engine.url.set(database=name).render_as_string(hide_password=False))
        files = sys.argv[1:] or ["tests/test_workspace.py", "tests/test_clustering.py", "tests/test_cluster_phase1.py",
                                 "tests/test_unified_scoring.py", "tests/test_query_intent.py", "tests/test_allocation.py", "tests/test_behavior.py", "tests/test_preferences.py", "tests/test_recommend.py", "tests/test_similar.py", "tests/test_personal_search.py", "tests/test_library.py", "tests/test_e2e_personalization.py", "tests/test_soft_dedup.py"]
        result = subprocess.run([sys.executable, "-m", "pytest", *files, "-q", "--disable-warnings", "-p", "no:cacheprovider", "--tb=short"],
                                env=env, cwd=Path(__file__).resolve().parents[1])
        return result.returncode
    finally:
        # Only the unique database created above is removed, including on failed tests.
        with admin.connect() as c:
            c.execute(text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=:n AND pid<>pg_backend_pid()"), {"n": name})
            c.execute(text(f'DROP DATABASE "{name}"'))
        admin.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
