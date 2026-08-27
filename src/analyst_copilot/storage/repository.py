"""Typed read/write access to the SQLite store. Callers outside this module
should not write raw SQL against `blocks`/`filings` — go through here so the
FTS5 index and evidence locators stay consistent."""

from __future__ import annotations

import sqlite3
from dataclasses import asdict

from analyst_copilot.ingestion.locator import Block, FilingRecord


def insert_filing(conn: sqlite3.Connection, filing: FilingRecord) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO filings
            (filing_id, doc_name, accession, company, cik, form_type, filing_date,
             period, source_url, raw_path, parser_version, ingested_at)
        VALUES (:filing_id, :doc_name, :accession, :company, :cik, :form_type,
                :filing_date, :period, :source_url, :raw_path,
                :parser_version, :ingested_at)
        """,
        asdict(filing),
    )
    conn.commit()


def insert_blocks(conn: sqlite3.Connection, blocks: list[Block]) -> None:
    conn.executemany(
        """
        INSERT OR REPLACE INTO blocks
            (block_id, filing_id, parent_id, previous_id, next_id,
             block_type, section_path, dom_xpath, char_start, char_end,
             text, table_id, row_index, col_index, row_header_path,
             col_header_path, rendered_page)
        VALUES (:block_id, :filing_id, :parent_id, :previous_id, :next_id,
                :block_type, :section_path, :dom_xpath, :char_start,
                :char_end, :text, :table_id, :row_index, :col_index,
                :row_header_path, :col_header_path, :rendered_page)
        """,
        [asdict(b) for b in blocks],
    )
    conn.commit()


def list_filings(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM filings ORDER BY ingested_at DESC"
    ).fetchall()


def get_filing(conn: sqlite3.Connection, filing_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM filings WHERE filing_id = ?", (filing_id,)
    ).fetchone()


def get_filing_by_doc_name(conn: sqlite3.Connection, doc_name: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM filings WHERE doc_name = ? ORDER BY ingested_at DESC LIMIT 1",
        (doc_name,),
    ).fetchone()


def get_block(conn: sqlite3.Connection, block_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM blocks WHERE block_id = ?", (block_id,)
    ).fetchone()


# Column weights for bm25(), in the order columns were declared in
# blocks_fts: text, section_path, row_header_path, col_header_path. A row's
# own header labels are a far more specific relevance signal than its raw
# displayed text or the section breadcrumb, which repeat near-verbatim
# across thousands of unrelated rows/paragraphs in a real filing.
_BM25_WEIGHTS = "1.0, 0.5, 4.0, 3.0"


def bm25_search(
    conn: sqlite3.Connection,
    filing_id: str,
    query: str,
    limit: int = 10,
    block_types: tuple[str, ...] | None = None,
) -> list[sqlite3.Row]:
    """Lexical search scoped to one filing (retrieval defaults to
    filing-local scope; see design doc "Retrieval: Hybrid, Routed, and
    Filing-Local"). Returns blocks ordered by BM25 rank (best first).
    `block_types`, if given, restricts to those block_type values (e.g.
    only "table_cell" for a numeric-lookup question)."""
    type_filter = ""
    params: list = [query, filing_id]
    if block_types:
        placeholders = ", ".join("?" for _ in block_types)
        type_filter = f"AND b.block_type IN ({placeholders})"
        params.extend(block_types)
    params.append(limit)

    return conn.execute(
        f"""
        SELECT b.*, bm25(blocks_fts, {_BM25_WEIGHTS}) AS rank
        FROM blocks_fts
        JOIN blocks b ON b.rowid = blocks_fts.rowid
        WHERE blocks_fts MATCH ? AND b.filing_id = ? {type_filter}
        ORDER BY rank
        LIMIT ?
        """,
        params,
    ).fetchall()
