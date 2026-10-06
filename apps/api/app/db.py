import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./market_intel.db")

IS_SQLITE = DATABASE_URL.startswith("sqlite")
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False, "timeout": 30} if IS_SQLITE else {},
    pool_pre_ping=not IS_SQLITE,
)

if IS_SQLITE:
    from sqlalchemy import event

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _):
        # WAL: readers never block the writer; busy_timeout: wait instead of "database is locked"
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=30000")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()
        # take transactions out of pysqlite's hands so we can BEGIN IMMEDIATE ourselves (below)
        dbapi_conn.isolation_level = None

    @event.listens_for(engine, "begin")
    def _sqlite_begin(conn):
        # Every transaction grabs the write lock up front and *waits* (busy_timeout) if another writer holds it.
        # Without this, a read-then-write transaction fails instantly ("database is locked") whenever another
        # thread committed in between (WAL snapshot conflict). Keep transactions short; never span network calls.
        conn.exec_driver_sql("BEGIN IMMEDIATE")
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def auto_migrate():
    """Add columns that exist in the models but not yet in the database (additive only, nullable).
    Keeps an existing SQLite/Postgres DB usable after upgrades; use Alembic for anything destructive."""
    from sqlalchemy import inspect, text

    with engine.begin() as conn:  # one connection for both reading the schema and altering it (no self-lock)
        insp = inspect(conn)
        existing_tables = set(insp.get_table_names())
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in have:
                    continue
                coltype = col.type.compile(dialect=engine.dialect)
                conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {coltype}'))


import threading as _threading
from contextlib import nullcontext as _nullcontext

# SQLite allows one writer at a time; serialize our heavy write paths (ingest, re-score) inside this process.
_write_lock = _threading.RLock()


def write_lock():
    return _write_lock if IS_SQLITE else _nullcontext()
