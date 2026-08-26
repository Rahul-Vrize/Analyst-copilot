"""Parse EDGAR HTML into the typed block tree defined in
analyst_copilot.ingestion.locator, without converting to PDF or plain text
(design doc: "Ingestion: Parse EDGAR HTML and Inline XBRL Without Loss").

Phase 1 scope (functional now):
  - section headings -> `section` blocks, tracked as a path stack
  - paragraphs / list items -> `paragraph` blocks
  - tables -> `table` + `table_row` + `table_cell` blocks, with a row-header
    (first cell of the row) and column-header (first row of the table) path

Known simplifications, left as TODOs for later phases rather than guessed:
  - rowspan/colspan are not expanded into repeated logical cells yet
  - footnote detection/linking is not implemented (see design doc section on
    footnote references) — footnote blocks are not yet emitted
  - numeric normalization (unit/scale/sign) happens in the fact/cell ledger
    build step (Phase 2), not here — this layer stores displayed text only
  - Inline XBRL fact extraction is a separate step (ingestion.xbrl_parser)
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import replace

from lxml import etree, html

from analyst_copilot.ingestion.locator import Block, FilingRecord

_HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
_BLOCK_TEXT_TAGS = {"p", "li", "caption"}
_SKIP_TAGS = {"script", "style", "head", "noscript"}
_WS_RE = re.compile(r"\s+")


def _norm_text(text: str | None) -> str:
    return _WS_RE.sub(" ", text or "").strip()


def _block_id(filing_id: str, dom_xpath: str, suffix: str = "") -> str:
    digest = hashlib.sha1(f"{dom_xpath}{suffix}".encode("utf-8")).hexdigest()[:16]
    return f"{filing_id}:{digest}"


class _Cursor:
    """Tracks running char offset into the canonical serialized text so
    every block gets a (char_start, char_end) span, and the section-path
    stack derived from heading depth."""

    def __init__(self) -> None:
        self.offset = 0
        self.section_stack: list[tuple[int, str]] = []  # (heading_level, text)

    def advance(self, text: str) -> tuple[int, int]:
        start = self.offset
        self.offset += len(text) + 1  # +1 for the joining newline
        return start, self.offset - 1

    def push_heading(self, level: int, text: str) -> None:
        while self.section_stack and self.section_stack[-1][0] >= level:
            self.section_stack.pop()
        self.section_stack.append((level, text))

    @property
    def section_path(self) -> str | None:
        if not self.section_stack:
            return None
        return " > ".join(t for _, t in self.section_stack)


def parse_filing_html(filing: FilingRecord, raw_bytes: bytes) -> list[Block]:
    """Walk the DOM in document order and emit the typed block tree."""
    tree = html.fromstring(raw_bytes)
    etree_doc = etree.ElementTree(tree)
    cursor = _Cursor()
    blocks: list[Block] = []

    for el in tree.iter():
        tag = el.tag if isinstance(el.tag, str) else None
        if tag is None or tag in _SKIP_TAGS:
            continue

        if tag in _HEADING_TAGS:
            text = _norm_text(el.text_content())
            if not text:
                continue
            level = int(tag[1])
            dom_xpath = etree_doc.getpath(el)
            start, end = cursor.advance(text)
            block_id = _block_id(filing.filing_id, dom_xpath)
            blocks.append(
                Block(
                    block_id=block_id,
                    filing_id=filing.filing_id,
                    parent_id=None,
                    previous_id=None,
                    next_id=None,
                    block_type="section",
                    section_path=cursor.section_path,
                    dom_xpath=dom_xpath,
                    char_start=start,
                    char_end=end,
                    text=text,
                )
            )
            cursor.push_heading(level, text)
            continue

        if tag in _BLOCK_TEXT_TAGS:
            text = _norm_text(el.text_content())
            if not text:
                continue
            dom_xpath = etree_doc.getpath(el)
            start, end = cursor.advance(text)
            blocks.append(
                Block(
                    block_id=_block_id(filing.filing_id, dom_xpath),
                    filing_id=filing.filing_id,
                    parent_id=None,
                    previous_id=None,
                    next_id=None,
                    block_type="paragraph",
                    section_path=cursor.section_path,
                    dom_xpath=dom_xpath,
                    char_start=start,
                    char_end=end,
                    text=text,
                )
            )
            continue

        if tag == "table":
            blocks.extend(_parse_table(filing, etree_doc, el, cursor))

    return blocks


def _parse_table(
    filing: FilingRecord,
    etree_doc: etree._ElementTree,
    table_el: etree._Element,
    cursor: _Cursor,
) -> list[Block]:
    dom_xpath = etree_doc.getpath(table_el)
    table_id = _block_id(filing.filing_id, dom_xpath)
    section_path = cursor.section_path

    caption_text = ""
    caption_el = table_el.find(".//caption")
    if caption_el is not None:
        caption_text = _norm_text(caption_el.text_content())

    blocks: list[Block] = [
        Block(
            block_id=table_id,
            filing_id=filing.filing_id,
            parent_id=None,
            previous_id=None,
            next_id=None,
            block_type="table",
            section_path=section_path,
            dom_xpath=dom_xpath,
            char_start=cursor.offset,
            char_end=cursor.offset,
            text=caption_text or None,
            table_id=table_id,
        )
    ]

    rows = table_el.findall(".//tr")
    header_texts: list[str] = []

    for row_idx, tr in enumerate(rows):
        cells = tr.findall("./td") + tr.findall("./th")
        cells.sort(key=lambda c: tr.index(c))
        is_header_row = row_idx == 0 and all(c.tag == "th" for c in cells if c.tag)

        row_xpath = etree_doc.getpath(tr)
        row_block_id = _block_id(filing.filing_id, row_xpath)
        row_text_parts = [_norm_text(c.text_content()) for c in cells]
        row_start, row_end = cursor.advance(" | ".join(row_text_parts))
        blocks.append(
            Block(
                block_id=row_block_id,
                filing_id=filing.filing_id,
                parent_id=table_id,
                previous_id=None,
                next_id=None,
                block_type="table_row",
                section_path=section_path,
                dom_xpath=row_xpath,
                char_start=row_start,
                char_end=row_end,
                text=" | ".join(p for p in row_text_parts if p) or None,
                table_id=table_id,
                row_index=row_idx,
            )
        )

        row_header = row_text_parts[0] if row_text_parts else None

        for col_idx, cell in enumerate(cells):
            cell_text = _norm_text(cell.text_content())
            cell_xpath = etree_doc.getpath(cell)
            start, end = cursor.advance(cell_text)
            col_header = (
                header_texts[col_idx]
                if is_header_row is False and col_idx < len(header_texts)
                else None
            )
            blocks.append(
                Block(
                    block_id=_block_id(filing.filing_id, cell_xpath),
                    filing_id=filing.filing_id,
                    parent_id=row_block_id,
                    previous_id=None,
                    next_id=None,
                    block_type="table_cell",
                    section_path=section_path,
                    dom_xpath=cell_xpath,
                    char_start=start,
                    char_end=end,
                    text=cell_text or None,
                    table_id=table_id,
                    row_index=row_idx,
                    col_index=col_idx,
                    row_header_path=row_header if col_idx > 0 else None,
                    col_header_path=col_header,
                )
            )

        if is_header_row:
            header_texts = row_text_parts

    return blocks
