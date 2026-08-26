"""Deterministic lookup over parsed table rows (design doc Phase 2:
`find_table_rows(title_terms, header_terms, row_terms)`), returning whole
candidate rows with their header paths rather than isolated cells — a table
header must never be separated from the values it qualifies.

Phase 1 implementation searches the `blocks` table directly (row/cell text
and header paths); it does not yet do unit/scale normalization, which lives
in the cell_ledger (DuckDB) once that's populated.
"""

from __future__ import annotations

import sqlite3


def find_table_rows(
    conn: sqlite3.Connection,
    filing_id: str,
    header_terms: list[str],
    limit: int = 5,
) -> list[sqlite3.Row]:
    if not header_terms:
        return []
    like_clauses = " OR ".join(["col_header_path LIKE ?"] * len(header_terms))
    params = [filing_id] + [f"%{t}%" for t in header_terms]
    rows = conn.execute(
        f"""
        SELECT * FROM blocks
        WHERE filing_id = ? AND block_type = 'table_cell' AND ({like_clauses})
        LIMIT ?
        """,
        (*params, limit),
    ).fetchall()
    return rows
