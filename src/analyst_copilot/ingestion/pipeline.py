"""End-to-end ingestion: load -> parse -> store. This is the function both
the CLI script and the Streamlit "Add filing" control call."""

from __future__ import annotations

import sqlite3
import time

from analyst_copilot.ingestion.edgar_loader import load_filing
from analyst_copilot.ingestion.html_parser import parse_filing_html
from analyst_copilot.ingestion.locator import FilingRecord
from analyst_copilot.storage import repository
from analyst_copilot.storage.db import get_sqlite_conn, init_db


def ingest_filing(
    path_or_url: str,
    *,
    doc_name: str | None = None,
    company: str | None = None,
    cik: str | None = None,
    form_type: str | None = None,
    filing_date: str | None = None,
    period: str | None = None,
    accession: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> tuple[FilingRecord, int, float]:
    """Returns (filing record, block count, elapsed seconds). Must complete
    well within the 10-minute budget for a single filing."""
    start = time.monotonic()
    init_db()

    record, raw_bytes = load_filing(
        path_or_url,
        doc_name=doc_name,
        company=company,
        cik=cik,
        form_type=form_type,
        filing_date=filing_date,
        period=period,
        accession=accession,
    )
    blocks = parse_filing_html(record, raw_bytes)

    owns_conn = conn is None
    conn = conn or get_sqlite_conn()
    try:
        repository.insert_filing(conn, record)
        repository.insert_blocks(conn, blocks)
    finally:
        if owns_conn:
            conn.close()

    elapsed = time.monotonic() - start
    return record, len(blocks), elapsed
