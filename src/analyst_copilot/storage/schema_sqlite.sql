-- Source of truth for filing metadata and the typed document tree.
-- Financial fact/cell analytics live in DuckDB (schema_duckdb.sql) instead,
-- so calculations can use SQL/Decimal-friendly numeric types.

CREATE TABLE IF NOT EXISTS filings (
    filing_id       TEXT PRIMARY KEY,      -- content hash of the raw source file
    doc_name        TEXT,                  -- source filename stem, e.g. "3M_2018_10K"
    accession       TEXT,
    company         TEXT,
    cik             TEXT,
    form_type       TEXT,
    filing_date     TEXT,
    period          TEXT,
    source_url      TEXT,                  -- original EDGAR URL, if any
    raw_path        TEXT NOT NULL,          -- path under data/raw/
    parser_version  TEXT NOT NULL,
    ingested_at     TEXT NOT NULL
);

-- One row per DOM object: paragraph, heading, list, table, table row,
-- table cell, footnote, caption, or continuation. Forms the typed tree via
-- parent_id / previous_id / next_id.
CREATE TABLE IF NOT EXISTS blocks (
    block_id        TEXT PRIMARY KEY,
    filing_id       TEXT NOT NULL REFERENCES filings(filing_id),
    parent_id       TEXT REFERENCES blocks(block_id),
    previous_id     TEXT REFERENCES blocks(block_id),
    next_id         TEXT REFERENCES blocks(block_id),
    block_type      TEXT NOT NULL,          -- section|paragraph|list|table|table_row|table_cell|footnote|caption
    section_path    TEXT,                   -- e.g. "Item 7 > Results of Operations"
    dom_xpath       TEXT NOT NULL,
    char_start      INTEGER,
    char_end        INTEGER,
    text            TEXT,                   -- normalized displayed text
    -- table-cell-specific fields (NULL for non-cell blocks)
    table_id        TEXT,
    row_index       INTEGER,
    col_index        INTEGER,
    row_header_path TEXT,
    col_header_path TEXT,
    rendered_page   INTEGER                 -- filled in only if page rendering is enabled
);

CREATE INDEX IF NOT EXISTS idx_filings_doc_name ON filings(doc_name);
CREATE INDEX IF NOT EXISTS idx_blocks_filing ON blocks(filing_id);
CREATE INDEX IF NOT EXISTS idx_blocks_type ON blocks(filing_id, block_type);
CREATE INDEX IF NOT EXISTS idx_blocks_table ON blocks(table_id);

-- Explicit local edges: footnote refs, cross-references, continuation
-- chains, table-to-narrative links, XBRL context links. Deliberately not a
-- general graph database (see design doc: "local typed edges, not a graph").
CREATE TABLE IF NOT EXISTS edges (
    edge_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    filing_id       TEXT NOT NULL REFERENCES filings(filing_id),
    src_block_id    TEXT NOT NULL,
    dst_block_id    TEXT NOT NULL,
    edge_type       TEXT NOT NULL           -- footnote_ref|cross_ref|continuation|table_narrative
);

-- Lexical (BM25-style) search over narrative text AND table header/label
-- paths. row_header_path/col_header_path are indexed as their own columns
-- (not just folded into `text`) so a specific metric label like "Capital
-- expenditures" can be weighted far above generic boilerplate that repeats
-- across thousands of unrelated rows in a real filing (e.g. "Amount",
-- "Location", "Millions") — see retrieval/bm25.py's column weights.
-- external content table keeps `blocks` as the single source of truth.
CREATE VIRTUAL TABLE IF NOT EXISTS blocks_fts USING fts5(
    text,
    section_path,
    row_header_path,
    col_header_path,
    content='blocks',
    content_rowid='rowid'
);

CREATE TRIGGER IF NOT EXISTS blocks_ai AFTER INSERT ON blocks BEGIN
    INSERT INTO blocks_fts(rowid, text, section_path, row_header_path, col_header_path)
    VALUES (new.rowid, new.text, new.section_path, new.row_header_path, new.col_header_path);
END;

CREATE TRIGGER IF NOT EXISTS blocks_ad AFTER DELETE ON blocks BEGIN
    INSERT INTO blocks_fts(blocks_fts, rowid, text, section_path, row_header_path, col_header_path)
    VALUES ('delete', old.rowid, old.text, old.section_path, old.row_header_path, old.col_header_path);
END;

CREATE TRIGGER IF NOT EXISTS blocks_au AFTER UPDATE ON blocks BEGIN
    INSERT INTO blocks_fts(blocks_fts, rowid, text, section_path, row_header_path, col_header_path)
    VALUES ('delete', old.rowid, old.text, old.section_path, old.row_header_path, old.col_header_path);
    INSERT INTO blocks_fts(rowid, text, section_path, row_header_path, col_header_path)
    VALUES (new.rowid, new.text, new.section_path, new.row_header_path, new.col_header_path);
END;
