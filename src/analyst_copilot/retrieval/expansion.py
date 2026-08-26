"""Local expansion: parent sections, neighboring rows, referenced
footnotes, explicit cross-links (design doc: retrieval path 4, "expand only
through local parents, neighboring rows, referenced footnotes, or explicit
cross-links"). Bounded — never a general graph traversal.

STATUS: stub. Implement once `edges` (storage/schema_sqlite.sql) is
populated by the parser; footnote/cross-reference detection is not yet
implemented in ingestion.html_parser.
"""

from __future__ import annotations

import sqlite3

MAX_EXPANSION_HOPS = 2


def expand_parent(conn: sqlite3.Connection, block_id: str) -> sqlite3.Row | None:
    row = conn.execute(
        "SELECT parent_id FROM blocks WHERE block_id = ?", (block_id,)
    ).fetchone()
    if not row or not row["parent_id"]:
        return None
    return conn.execute(
        "SELECT * FROM blocks WHERE block_id = ?", (row["parent_id"],)
    ).fetchone()


def expand_neighbors(
    conn: sqlite3.Connection, block_id: str
) -> tuple[sqlite3.Row | None, sqlite3.Row | None]:
    row = conn.execute(
        "SELECT previous_id, next_id FROM blocks WHERE block_id = ?", (block_id,)
    ).fetchone()
    if not row:
        return None, None
    prev = (
        conn.execute("SELECT * FROM blocks WHERE block_id = ?", (row["previous_id"],)).fetchone()
        if row["previous_id"]
        else None
    )
    nxt = (
        conn.execute("SELECT * FROM blocks WHERE block_id = ?", (row["next_id"],)).fetchone()
        if row["next_id"]
        else None
    )
    return prev, nxt
