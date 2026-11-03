"""Additive, repeatable migration for the free LLM providers: python -m app.llm_migrate.

Provider entries are now "<provider>:<model>" (Groq / Cerebras count free limits per model), so the provider columns of the shared
state and of the call ledger widen from 24 to 64 characters. PostgreSQL widens a VARCHAR in place (catalog change, no rewrite, rows
unchanged); SQLite does not enforce lengths, nothing to do."""
from sqlalchemy import text

COLUMNS = (("llm_provider_state", "provider"), ("external_api_calls", "provider"))


def migrate(bind=None):
    from .db import engine

    eng = bind or engine
    if eng.dialect.name != "postgresql":
        return False
    with eng.begin() as c:
        c.execute(text("SELECT pg_advisory_xact_lock(727005)"))  # API + worker start together
        for table, col in COLUMNS:
            n = c.execute(text("SELECT character_maximum_length FROM information_schema.columns WHERE table_name = :t AND column_name = :c"),
                          {"t": table, "c": col}).scalar()
            if n is not None and n < 64:
                c.execute(text(f'ALTER TABLE "{table}" ALTER COLUMN "{col}" TYPE VARCHAR(64)'))
    return True


if __name__ == "__main__":
    print("widened" if migrate() else "nothing to do (not PostgreSQL)")
