"""Connection helpers for the two storage engines.

SQLite is the source of truth for filing metadata and the typed document
tree, plus FTS5 lexical search. DuckDB holds the fact/cell ledgers for
reproducible SQL analytics and Decimal-safe arithmetic. See the design doc
section "Representation and Storage" for the rationale.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import duckdb

from analyst_copilot.config import settings

_SQLITE_SCHEMA = Path(__file__).with_name("schema_sqlite.sql")
_DUCKDB_SCHEMA = Path(__file__).with_name("schema_duckdb.sql")


def get_sqlite_conn(db_path: Path | None = None) -> sqlite3.Connection:
    db_path = db_path or settings.sqlite_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_duckdb_conn(db_path: Path | None = None) -> duckdb.DuckDBPyConnection:
    db_path = db_path or settings.duckdb_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(db_path))


def init_db() -> None:
    """Create tables/indexes/FTS5 triggers if they don't already exist.
    Idempotent — safe to call on every startup."""
    settings.ensure_dirs()

    with get_sqlite_conn() as conn:
        conn.executescript(_SQLITE_SCHEMA.read_text(encoding="utf-8"))

    with get_duckdb_conn() as conn:
        conn.execute(_DUCKDB_SCHEMA.read_text(encoding="utf-8"))


if __name__ == "__main__":
    init_db()
    print(f"Initialized SQLite at {settings.sqlite_path}")
    print(f"Initialized DuckDB at {settings.duckdb_path}")
