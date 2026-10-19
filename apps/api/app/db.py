import logging
import os
import threading
import time
import traceback
from contextlib import contextmanager
from contextvars import ContextVar

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from starlette.requests import Request

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./market_intel.db")

IS_SQLITE = DATABASE_URL.startswith("sqlite")
WRITE_LOCK_WARN_S = float(os.getenv("WRITE_LOCK_WARN_S", "5"))
engine = create_engine(
    DATABASE_URL,
    # Postgres: timezone=UTC — the app stores naive utcnow(); the server default (Asia/Bangkok) would shift timestamps
    connect_args={"check_same_thread": False, "timeout": 60} if IS_SQLITE else {"options": "-c timezone=UTC"},
    pool_pre_ping=not IS_SQLITE,
    **({} if IS_SQLITE else {"pool_size": int(os.getenv("DB_POOL_SIZE", "20")), "max_overflow": int(os.getenv("DB_MAX_OVERFLOW", "20"))}),
)

if not IS_SQLITE:
    from sqlalchemy import String
    from sqlalchemy.ext.compiler import compiles

    @compiles(String, "postgresql")
    def _pg_unbounded_varchar(type_, compiler, **kw):
        # SQLite never enforced String(n); keep that — a long ad title must not fail an ingest batch on Postgres
        return "VARCHAR"

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

_ingest_tlock = threading.Lock()


@contextmanager
def ingest_lock():
    """Serialize ingest (get-or-create of products / advertisers / ads) across threads *and* processes — the API
    (live searches) and the worker (scheduler) both ingest. Postgres: an advisory lock on its own connection, since
    ingest commits several times inside. SQLite: BEGIN IMMEDIATE already serializes processes; the thread lock suffices.
    ponytail: one global lock — fetches run outside it, so only the short DB part is serialized."""
    with _ingest_tlock:
        if IS_SQLITE:
            yield
            return
        from sqlalchemy import text

        with engine.connect() as conn:
            conn.execute(text("SELECT pg_advisory_lock(727001)"))
            conn.commit()
            try:
                yield
            finally:
                conn.execute(text("SELECT pg_advisory_unlock(727001)"))
                conn.commit()


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


def get_db(request: Request = None):
    db = SessionLocal()
    if request is not None and getattr(request.state, "user", None):
        db.info["user_id"] = request.state.user["id"]
        db.info["role"] = request.state.user["role"]
    try:
        yield db
    finally:
        db.close()


def auto_migrate():
    """Create missing tables, then add columns that exist in the models but not yet in the database (additive only, nullable).
    Keeps an existing SQLite/Postgres DB usable after upgrades; use Alembic for anything destructive."""
    from sqlalchemy import inspect, text
    from .workspace import models as workspace_models  # register additive account / tracker tables

    with engine.begin() as conn:  # one connection for both reading the schema and altering it (no self-lock)
        if not IS_SQLITE:  # API and worker start together: one migrates, the other waits (concurrent DDL collides)
            conn.execute(text("SELECT pg_advisory_xact_lock(727002)"))
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
        Base.metadata.create_all(conn)
        if not IS_SQLITE and "ads" in existing_tables:  # engagement columns became nullable (NULL = never reported, not 0)
            for col in ("likes", "comments_count", "shares"):
                conn.execute(text(f'ALTER TABLE ads ALTER COLUMN "{col}" DROP NOT NULL'))
        for ddl in ("ix_ads_active_last_seen ON ads (is_active, last_seen_at)", "ix_ads_product_active ON ads (product_id, is_active)",
                    "ix_creatives_status_type_collected ON creatives (status, type, collected_at)",
                    "ix_alerts_read_created ON alerts (is_read, created_at)"):
            if ddl.split(" ON ")[1].split(" ")[0] in existing_tables:
                conn.execute(text(f"CREATE INDEX IF NOT EXISTS {ddl}"))
# No in-process writer lock: BEGIN IMMEDIATE already serializes SQLite writers, and a second (Python) lock taken in a
# different order deadlocked against it (request: SQLite → RLock, ingest: RLock → SQLite) until busy_timeout → HTTP 500.
