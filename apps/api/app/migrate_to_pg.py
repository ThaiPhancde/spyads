"""Copy a SQLite database into an empty Postgres one (DATABASE_URL = the Postgres target).

    python -m app.migrate_to_pg market_intel.db

Stop the API / worker first. Re-runnable: refuses to load into a table that already has rows unless --replace.
Same script moves the data to a VPS later (point DATABASE_URL at the remote Postgres).
"""
import sys

from sqlalchemy import create_engine, func, inspect, select, text

from . import models  # noqa: F401 — registers every table on Base.metadata
from .db import IS_SQLITE, Base, engine


def _clean(v):
    return v.replace("\x00", "") if isinstance(v, str) else v  # Postgres text cannot hold NUL; SQLite can


def main(path: str, replace: bool = False):
    assert not IS_SQLITE, "DATABASE_URL must point at Postgres"
    src = create_engine(f"sqlite:///{path}")
    src_tables = set(inspect(src).get_table_names())
    Base.metadata.create_all(engine)
    with engine.begin() as dst:
        dst.execute(text("SET session_replication_role = replica"))  # no FK / trigger checks while bulk loading
        for t in Base.metadata.sorted_tables:
            if t.name not in src_tables:
                print(f"{t.name:32} (not in SQLite)")
                continue
            if dst.scalar(select(func.count()).select_from(t)):
                if not replace:
                    sys.exit(f"{t.name} already has rows — pass --replace to truncate and reload")
                dst.execute(text(f'TRUNCATE "{t.name}" CASCADE'))
            have = {c["name"] for c in inspect(src).get_columns(t.name)}
            cols = [c for c in t.columns if c.name in have]  # columns added after this SQLite file was last migrated stay NULL
            # NOT NULL columns that auto_migrate added later as nullable hold NULLs in old rows: use the model default
            fill = {c.name: c.default.arg for c in cols if not c.nullable and c.default is not None and c.default.is_scalar}
            n = 0
            with src.connect() as s:
                res = s.execution_options(stream_results=True, yield_per=2000).execute(select(*cols))
                for part in res.mappings().partitions():
                    dst.execute(t.insert(), [{k: _clean(fill[k] if v is None and k in fill else v) for k, v in r.items()} for r in part])
                    n += len(part)
            pk = list(t.primary_key.columns)
            if len(pk) == 1 and pk[0].autoincrement in (True, "auto") and pk[0].type.python_type is int:
                dst.execute(text(f"SELECT setval(pg_get_serial_sequence('\"{t.name}\"', '{pk[0].name}'), "
                                 f"COALESCE((SELECT MAX(\"{pk[0].name}\") FROM \"{t.name}\"), 0) + 1, false)"))
            print(f"{t.name:32} {n:>9,}")
    print("done")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    main(args[0] if args else "market_intel.db", replace="--replace" in sys.argv)
