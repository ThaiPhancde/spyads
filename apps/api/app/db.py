import logging
import os
import threading
import time
import traceback
from contextlib import contextmanager
from contextvars import ContextVar

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./market_intel.db")

IS_SQLITE = DATABASE_URL.startswith("sqlite")
WRITE_LOCK_WARN_S = float(os.getenv("WRITE_LOCK_WARN_S", "5"))
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False, "timeout": 60} if IS_SQLITE else {},
    pool_pre_ping=not IS_SQLITE,
)

if IS_SQLITE:
    from sqlalchemy import event

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _):
        # WAL: readers never block the writer; busy_timeout: wait instead of "database is locked"
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=60000")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()
        # take transactions out of pysqlite's hands so we can BEGIN IMMEDIATE ourselves (below)
        dbapi_conn.isolation_level = None

    @event.listens_for(engine, "begin")
    def _sqlite_begin(conn):
        # Every transaction grabs the write lock up front and *waits* (busy_timeout) if another writer holds it.
        # Without this, a read-then-write transaction fails instantly ("database is locked") whenever another
        # thread committed in between (WAL snapshot conflict). Keep transactions short; never span network calls.
        # GET requests only read: a deferred BEGIN never waits for the writer (WAL), so pages stay fast while
        # ingest / media workers write — waiting here used to exceed the web proxy timeout (HTTP 500).
        conn.exec_driver_sql("BEGIN" if READ_ONLY.get() else "BEGIN IMMEDIATE")
        if not READ_ONLY.get():  # ponytail: write-lock watchdog — who held the SQLite writer for too long (see _sqlite_end)
            conn.info["w_t0"] = time.time()
            conn.info["w_stack"] = "".join(f for f in traceback.format_stack(limit=60) if "apps\\api\\app" in f or "apps/api/app" in f)

    @event.listens_for(engine, "commit")
    @event.listens_for(engine, "rollback")
    def _sqlite_end(conn):
        t0 = conn.info.pop("w_t0", None)
        if t0 and time.time() - t0 > WRITE_LOCK_WARN_S:
            logging.getLogger("app.db").warning("write transaction held the SQLite lock %.1fs — thread %s | %s",
                                                time.time() - t0, threading.current_thread().name, conn.info.pop("w_stack", ""))
        conn.info.pop("w_stack", None)
READ_ONLY: ContextVar[bool] = ContextVar("db_read_only", default=False)


@contextmanager
def read_only():
    """Pure-read work outside a request (scheduler scans, media queue): don't take the SQLite write lock."""
    tok = READ_ONLY.set(True)
    try:
        yield
    finally:
        READ_ONLY.reset(tok)


class ReadOnlyGets:
    """ASGI middleware: mark GET/HEAD requests read-only for the SQLite BEGIN choice above."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in ("GET", "HEAD"):
            return await self.app(scope, receive, send)
        with read_only():
            await self.app(scope, receive, send)
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
            for idx in table.indexes:  # index=True columns added above (or by an older upgrade) never got their index
                idx.create(conn, checkfirst=True)
        for ddl in ("ix_ads_active_last_seen ON ads (is_active, last_seen_at)", "ix_ads_product_active ON ads (product_id, is_active)",
                    "ix_creatives_status_type_collected ON creatives (status, type, collected_at)",
                    "ix_alerts_read_created ON alerts (is_read, created_at)"):
            if ddl.split(" ON ")[1].split(" ")[0] in existing_tables:
                conn.execute(text(f"CREATE INDEX IF NOT EXISTS {ddl}"))
# No in-process writer lock: BEGIN IMMEDIATE already serializes SQLite writers, and a second (Python) lock taken in a
# different order deadlocked against it (request: SQLite → RLock, ingest: RLock → SQLite) until busy_timeout → HTTP 500.
