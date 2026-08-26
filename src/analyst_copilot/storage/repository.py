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
            (filing_id, accession, company, cik, form_type, filing_date,
             period, source_url, raw_path, parser_version, ingested_at)
        VALUES (:filing_id, :accession, :company, :cik, :form_type,
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


def get_block(conn: sqlite3.Connection, block_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM blocks WHERE block_id = ?", (block_id,)
    ).fetchone()


def bm25_search(
    conn: sqlite3.Connection, filing_id: str, query: str, limit: int = 10
) -> list[sqlite3.Row]:
    """Lexical search scoped to one filing (retrieval defaults to
    filing-local scope; see design doc "Retrieval: Hybrid, Routed, and
    Filing-Local"). Returns blocks ordered by BM25 rank (best first)."""
    return conn.execute(
        """
        SELECT b.*, bm25(blocks_fts) AS rank
        FROM blocks_fts
        JOIN blocks b ON b.rowid = blocks_fts.rowid
        WHERE blocks_fts MATCH ? AND b.filing_id = ?
        ORDER BY rank
        LIMIT ?
        """,
        (query, filing_id, limit),
    ).fetchall()
