"""Typed records for the filing tree and its evidence locators.

Every answer must carry two identities (design doc, "Ingestion" section):
a semantic identity (e.g. "revenue, FY2023, segment=Cloud") and a source
identity (accession, DOM path, table ID, row, column, XBRL context). These
dataclasses carry the source identity; the semantic identity lives in
analyst_copilot.reasoning.answer_plan.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FilingRecord:
    filing_id: str  # sha256 content hash of the raw source file
    accession: str | None
    company: str | None
    cik: str | None
    form_type: str | None
    filing_date: str | None
    period: str | None
    source_url: str | None
    raw_path: str
    parser_version: str
    ingested_at: str


@dataclass(frozen=True)
class Block:
    """One node in the typed document tree: section, paragraph, list,
    table, table_row, table_cell, footnote, or caption."""

    block_id: str
    filing_id: str
    parent_id: str | None
    previous_id: str | None
    next_id: str | None
    block_type: str
    section_path: str | None
    dom_xpath: str
    char_start: int | None
    char_end: int | None
    text: str | None
    # table-cell-specific (None for everything else)
    table_id: str | None = None
    row_index: int | None = None
    col_index: int | None = None
    row_header_path: str | None = None
    col_header_path: str | None = None
    rendered_page: int | None = None


@dataclass(frozen=True)
class EvidenceLocator:
    """The exact-location record required on every answer (design doc,
    "Verification and Evidence"). `rendered_page` is optional — native HTML
    has no authoritative page number unless a canonical render is pinned."""

    accession: str | None
    filing_url: str | None
    section_path: str | None
    dom_id_or_xpath: str
    char_start: int | None
    char_end: int | None
    table_id: str | None = None
    row: int | None = None
    column: int | None = None
    xbrl_fact_id: str | None = None
    rendered_page: int | None = None

    @classmethod
    def from_block(cls, filing: FilingRecord, block: Block) -> "EvidenceLocator":
        return cls(
            accession=filing.accession,
            filing_url=filing.source_url,
            section_path=block.section_path,
            dom_id_or_xpath=block.dom_xpath,
            char_start=block.char_start,
            char_end=block.char_end,
            table_id=block.table_id,
            row=block.row_index,
            column=block.col_index,
            rendered_page=block.rendered_page,
        )
