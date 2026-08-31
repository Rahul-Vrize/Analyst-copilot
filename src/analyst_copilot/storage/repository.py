"""Repository - the only place SQL is written (§24.2).

Every write is an UPSERT keyed on a stable id, so re-ingesting a filing is
idempotent: the same filing produces the same ids and simply overwrites. That
matters because the product lets a user re-upload a document, and because a
failed ingest must be safe to retry.

Ids are derived, never random:
    filing  '3M_2018_10K'
    page    '3M_2018_10K#p59'
    block   '3M_2018_10K#p59.b12'
    table   '3M_2018_10K#p59.t2'
    cell    '3M_2018_10K#p59.t2.r4c3'
A page id therefore reads as a citation, and a stale row cannot survive a
re-ingest under a different id.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

import psycopg
from psycopg.types.json import Jsonb

PARSER_VERSION = "2026-08-31.1"


# Id construction lives in one place for the whole system - see ids.py.
from ..ids import block_id, cell_id, page_id, section_id, table_id  # noqa: F401,E402


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------
def upsert_filing(conn: psycopg.Connection, filing: dict[str, Any]) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO filings (
                doc_id, file_path, content_hash, company_name, company_slug,
                form_type, fiscal_year, fiscal_quarter, period_label,
                filing_date, coverage_years, page_count, has_xbrl,
                ingest_status, ingest_progress, parser_version
            ) VALUES (
                %(doc_id)s, %(file_path)s, %(content_hash)s, %(company_name)s,
                %(company_slug)s, %(form_type)s, %(fiscal_year)s,
                %(fiscal_quarter)s, %(period_label)s, %(filing_date)s,
                %(coverage_years)s, %(page_count)s, %(has_xbrl)s,
                %(ingest_status)s, %(ingest_progress)s, %(parser_version)s
            )
            ON CONFLICT (doc_id) DO UPDATE SET
                file_path       = EXCLUDED.file_path,
                content_hash    = EXCLUDED.content_hash,
                company_name    = EXCLUDED.company_name,
                company_slug    = EXCLUDED.company_slug,
                form_type       = EXCLUDED.form_type,
                fiscal_year     = EXCLUDED.fiscal_year,
                fiscal_quarter  = EXCLUDED.fiscal_quarter,
                period_label    = EXCLUDED.period_label,
                filing_date     = EXCLUDED.filing_date,
                coverage_years  = EXCLUDED.coverage_years,
                page_count      = EXCLUDED.page_count,
                has_xbrl        = EXCLUDED.has_xbrl,
                ingest_status   = EXCLUDED.ingest_status,
                ingest_progress = EXCLUDED.ingest_progress,
                parser_version  = EXCLUDED.parser_version
            """,
            filing,
        )


def set_ingest_status(
    conn: psycopg.Connection,
    doc_id: str,
    status: str,
    progress: float,
    error: str | None = None,
) -> None:
    """Drives the visible processing indicator the brief grades."""
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE filings
                  SET ingest_status = %s, ingest_progress = %s, ingest_error = %s
                WHERE doc_id = %s""",
            (status, progress, error, doc_id),
        )


def content_hash_of(conn: psycopg.Connection, doc_id: str) -> str | None:
    """Used to skip re-parsing an unchanged filing (idempotent re-ingest)."""
    with conn.cursor() as cur:
        cur.execute("SELECT content_hash FROM filings WHERE doc_id = %s", (doc_id,))
        row = cur.fetchone()
        return row["content_hash"] if row else None


def seed_company_aliases(conn: psycopg.Connection, aliases: dict[str, list[str]]) -> int:
    """Load data/company_aliases.yaml into the catalog.

    This table is real-world knowledge (AMEX = American Express) that any user
    would rely on, and it is extended for a newly uploaded company - it is not
    benchmark knowledge (§25.2).
    """
    rows = [(slug, alias) for slug, values in aliases.items() for alias in values]
    with conn.cursor() as cur:
        cur.executemany(
            """INSERT INTO company_aliases (company_slug, alias)
               VALUES (%s, %s) ON CONFLICT DO NOTHING""",
            rows,
        )
    return len(rows)


def load_catalog(conn: psycopg.Connection) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT doc_id, company_slug, form_type, fiscal_year, fiscal_quarter,
                      period_label, filing_date, coverage_years, page_count
                 FROM filings
                WHERE ingest_status = 'ready'
                ORDER BY doc_id"""
        )
        return cur.fetchall()


def load_aliases(conn: psycopg.Connection) -> dict[str, list[str]]:
    with conn.cursor() as cur:
        cur.execute("SELECT company_slug, alias FROM company_aliases")
        out: dict[str, list[str]] = {}
        for row in cur.fetchall():
            out.setdefault(row["company_slug"], []).append(row["alias"])
    return {k: sorted(v) for k, v in out.items()}


# ---------------------------------------------------------------------------
# Content - bulk writes
# ---------------------------------------------------------------------------
def replace_document_content(conn: psycopg.Connection, doc_id: str) -> None:
    """Clear a filing's derived content before re-ingest.

    Deletion order respects the foreign keys: cells -> tables/blocks -> pages.
    Without this a re-ingest that produces FEWER pages would leave orphans that
    are still reachable by retrieval - a stale citation is a wrong location.
    """
    with conn.cursor() as cur:
        cur.execute(
            """DELETE FROM table_cells
                WHERE table_id IN (SELECT table_id FROM tables WHERE doc_id = %s)""",
            (doc_id,),
        )
        for stmt in (
            "DELETE FROM tables WHERE doc_id = %s",
            "DELETE FROM blocks WHERE doc_id = %s",
            "DELETE FROM edges  WHERE doc_id = %s",
            "DELETE FROM facts  WHERE doc_id = %s",
            "DELETE FROM pages  WHERE doc_id = %s",
            "DELETE FROM sections WHERE doc_id = %s",
        ):
            cur.execute(stmt, (doc_id,))


def insert_pages(conn: psycopg.Connection, rows: Sequence[dict[str, Any]]) -> None:
    if not rows:
        return
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO pages (
                page_id, doc_id, section_id, page_seq, page_printed,
                prev_page_id, next_page_id, char_start, char_end,
                raw_text, summary, context_header, lexical_text,
                has_tables, token_est
            ) VALUES (
                %(page_id)s, %(doc_id)s, %(section_id)s, %(page_seq)s,
                %(page_printed)s, %(prev_page_id)s, %(next_page_id)s,
                %(char_start)s, %(char_end)s, %(raw_text)s, %(summary)s,
                %(context_header)s, %(lexical_text)s, %(has_tables)s,
                %(token_est)s
            )
            ON CONFLICT (page_id) DO UPDATE SET
                raw_text       = EXCLUDED.raw_text,
                lexical_text   = EXCLUDED.lexical_text,
                context_header = EXCLUDED.context_header,
                has_tables     = EXCLUDED.has_tables,
                token_est      = EXCLUDED.token_est
            """,
            rows,
        )


def insert_blocks(conn: psycopg.Connection, rows: Sequence[dict[str, Any]]) -> None:
    if not rows:
        return
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO blocks (
                block_id, page_id, doc_id, section_id, order_idx,
                prev_block_id, next_block_id, block_type, text,
                char_start, char_end, table_id
            ) VALUES (
                %(block_id)s, %(page_id)s, %(doc_id)s, %(section_id)s,
                %(order_idx)s, %(prev_block_id)s, %(next_block_id)s,
                %(block_type)s, %(text)s, %(char_start)s, %(char_end)s,
                %(table_id)s
            )
            ON CONFLICT (block_id) DO NOTHING
            """,
            rows,
        )


def insert_tables(conn: psycopg.Connection, rows: Sequence[dict[str, Any]]) -> None:
    if not rows:
        return
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO tables (
                table_id, page_id, doc_id, section_id, order_idx, caption,
                units_note, n_rows, n_cols, markdown, is_data_table,
                alignment_ok, header_tokens, value_col_count
            ) VALUES (
                %(table_id)s, %(page_id)s, %(doc_id)s, %(section_id)s,
                %(order_idx)s, %(caption)s, %(units_note)s, %(n_rows)s,
                %(n_cols)s, %(markdown)s, %(is_data_table)s, %(alignment_ok)s,
                %(header_tokens)s, %(value_col_count)s
            )
            ON CONFLICT (table_id) DO NOTHING
            """,
            rows,
        )


def insert_table_cells(conn: psycopg.Connection, rows: Sequence[dict[str, Any]]) -> None:
    """Populated ONLY where tables.alignment_ok is true - the fail-closed rule."""
    if not rows:
        return
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO table_cells (
                cell_id, table_id, row_idx, col_idx, row_header_path,
                col_header_path, raw_text, numeric_value, sign, scale, unit
            ) VALUES (
                %(cell_id)s, %(table_id)s, %(row_idx)s, %(col_idx)s,
                %(row_header_path)s, %(col_header_path)s, %(raw_text)s,
                %(numeric_value)s, %(sign)s, %(scale)s, %(unit)s
            )
            ON CONFLICT (cell_id) DO NOTHING
            """,
            rows,
        )


def insert_sections(conn: psycopg.Connection, rows: Sequence[dict[str, Any]]) -> None:
    if not rows:
        return
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO sections (
                section_id, doc_id, parent_id, level, ordinal, raw_title,
                clean_title, summary, kind, stmt_type, source,
                page_start, page_end
            ) VALUES (
                %(section_id)s, %(doc_id)s, %(parent_id)s, %(level)s,
                %(ordinal)s, %(raw_title)s, %(clean_title)s, %(summary)s,
                %(kind)s, %(stmt_type)s, %(source)s, %(page_start)s, %(page_end)s
            )
            ON CONFLICT (section_id) DO UPDATE SET
                clean_title = EXCLUDED.clean_title,
                summary     = EXCLUDED.summary,
                kind        = EXCLUDED.kind,
                stmt_type   = EXCLUDED.stmt_type
            """,
            rows,
        )


# ---------------------------------------------------------------------------
# Reads used by retrieval and by gate G1
# ---------------------------------------------------------------------------
def page_text(conn: psycopg.Connection, pid: str) -> str | None:
    """Gate G1 checks a quote against `raw_text` - NEVER against `summary` or
    `lexical_text`, which are paraphrases and would let an invented quote pass."""
    with conn.cursor() as cur:
        cur.execute("SELECT raw_text FROM pages WHERE page_id = %s", (pid,))
        row = cur.fetchone()
        return row["raw_text"] if row else None


def pages_for_bm25(
    conn: psycopg.Connection, doc_ids: Iterable[str] | None = None
) -> list[dict[str, Any]]:
    """Feed the in-memory BM25 index (D10: no BM25 extension on Azure)."""
    with conn.cursor() as cur:
        if doc_ids:
            cur.execute(
                """SELECT page_id, doc_id, page_seq, lexical_text, raw_text,
                          context_header, page_printed
                     FROM pages WHERE doc_id = ANY(%s) ORDER BY doc_id, page_seq""",
                (list(doc_ids),),
            )
        else:
            cur.execute(
                """SELECT page_id, doc_id, page_seq, lexical_text, raw_text,
                          context_header, page_printed
                     FROM pages ORDER BY doc_id, page_seq"""
            )
        return cur.fetchall()


def corpus_stats(conn: psycopg.Connection) -> dict[str, int]:
    out: dict[str, int] = {}
    with conn.cursor() as cur:
        for table in (
            "filings", "pages", "blocks", "tables", "table_cells",
            "sections", "facts", "company_aliases",
        ):
            cur.execute(f"SELECT count(*) AS n FROM {table}")  # noqa: S608 - fixed list
            out[table] = cur.fetchone()["n"]
    return out
