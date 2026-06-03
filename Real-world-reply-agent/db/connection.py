"""Database connection helper.

Supports two backends, selected by DATABASE_URL:
  * sqlite:///...        — local dev (default). Uses the stdlib sqlite3 driver.
  * postgresql://...     — production (Railway Postgres). Uses psycopg (v3).

Call sites were originally written against sqlite3's API (``conn.execute(sql, params)``
with ``?`` placeholders, ``conn.commit()``, ``conn.close()``). To avoid rewriting
every query, the Postgres path returns a thin wrapper that:
  * translates ``?`` placeholders to ``%s`` (psycopg's paramstyle), and
  * proxies everything else (commit/close/cursor/...) straight through.

Rows come back dict-like in both backends (sqlite3.Row / psycopg dict_row), so
``row["col"]`` and ``dict(row)`` work uniformly.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from config import settings

SCHEMA_SQLITE = Path(__file__).parent / "schema.sql"
SCHEMA_POSTGRES = Path(__file__).parent / "schema_postgres.sql"


class _PgConnection:
    """Adapter that makes a psycopg3 connection behave like the sqlite3 one our
    call sites expect. Only ``execute`` needs special handling (placeholder
    translation); everything else is proxied to the underlying connection."""

    def __init__(self, raw) -> None:
        self._raw = raw

    def execute(self, sql: str, params=None):
        sql = sql.replace("?", "%s")
        if params is None:
            return self._raw.execute(sql)
        return self._raw.execute(sql, params)

    def __getattr__(self, name):
        # commit, close, rollback, cursor, etc. all live on the raw connection
        return getattr(self._raw, name)

    def __enter__(self) -> "_PgConnection":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def get_connection():
    """Open a new connection to the configured database."""
    if settings.is_postgres:
        import psycopg
        from psycopg.rows import dict_row

        # connect_timeout prevents blocking entrypoint.sh startup when Postgres
        # is briefly unavailable (e.g. Railway container ordering at boot).
        raw = psycopg.connect(settings.database_url, row_factory=dict_row, connect_timeout=10)
        return _PgConnection(raw)

    db_path = settings.sqlite_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def _split_statements(sql: str) -> list[str]:
    """Split a schema script into individual statements for psycopg, which (by
    default) refuses multiple commands in one execute. Drops ``--`` comment lines."""
    lines = [ln for ln in sql.splitlines() if not ln.strip().startswith("--")]
    body = "\n".join(lines)
    return [s.strip() for s in body.split(";") if s.strip()]


def init_db():
    """Create all tables if they don't exist. Idempotent — safe on every boot."""
    if settings.is_postgres:
        schema = SCHEMA_POSTGRES.read_text(encoding="utf-8")
        conn = get_connection()
        try:
            for stmt in _split_statements(schema):
                conn.execute(stmt)
            conn.commit()
        finally:
            conn.close()
        return "postgres"

    schema_sql = SCHEMA_SQLITE.read_text(encoding="utf-8")
    conn = get_connection()
    try:
        conn.executescript(schema_sql)
        conn.commit()
    finally:
        conn.close()
    return settings.sqlite_path


if __name__ == "__main__":
    target = init_db()
    print(f"Initialized database: {target}")
