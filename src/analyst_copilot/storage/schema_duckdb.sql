-- Financial fact and cell analytics: one record per displayed table cell and
-- one per XBRL fact, both carrying full header context and normalized
-- values so that "1,234" is never ambiguous between thousands/millions/%.

CREATE TABLE IF NOT EXISTS cell_ledger (
    filing_id       VARCHAR NOT NULL,
    block_id        VARCHAR NOT NULL,        -- FK to sqlite blocks.block_id (table_cell)
    table_id        VARCHAR,
    row_index       INTEGER,
    col_index       INTEGER,
    row_header_path VARCHAR,
    col_header_path VARCHAR,
    displayed_text  VARCHAR,
    value           DECIMAL(38, 8),
    unit            VARCHAR,                 -- USD, shares, percent, ...
    scale           VARCHAR,                 -- ones, thousands, millions
    sign            INTEGER,                 -- 1 or -1
    footnote_refs   VARCHAR                  -- comma-separated footnote block_ids
);

CREATE TABLE IF NOT EXISTS fact_ledger (
    filing_id       VARCHAR NOT NULL,
    block_id        VARCHAR,                 -- FK to sqlite blocks.block_id, source ix element
    qname           VARCHAR NOT NULL,        -- e.g. us-gaap:Revenues
    context_id      VARCHAR,
    dimensions      VARCHAR,                 -- JSON-encoded XBRL dimension/member pairs
    period_start    VARCHAR,
    period_end      VARCHAR,
    unit            VARCHAR,
    decimals        INTEGER,
    scale           VARCHAR,
    sign            INTEGER,
    value            DECIMAL(38, 8)
);

CREATE INDEX IF NOT EXISTS idx_cell_ledger_filing ON cell_ledger(filing_id);
CREATE INDEX IF NOT EXISTS idx_fact_ledger_filing_qname ON fact_ledger(filing_id, qname);
