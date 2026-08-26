from datetime import datetime, timezone

from analyst_copilot.ingestion.html_parser import parse_filing_html
from analyst_copilot.ingestion.locator import FilingRecord

SAMPLE_HTML = b"""
<html><body>
<h1>Item 7. Management Discussion</h1>
<p>Revenue grew due to strong demand in North America.</p>
<h2>Results of Operations</h2>
<table>
  <tr><th>Metric</th><th>FY2022</th><th>FY2023</th></tr>
  <tr><td>Revenue</td><td>100</td><td>120</td></tr>
  <tr><td>Capex</td><td>10</td><td>15</td></tr>
</table>
</body></html>
"""


def _fixture_filing() -> FilingRecord:
    return FilingRecord(
        filing_id="testhash",
        accession="0000000000-24-000001",
        company="Test Co",
        cik="1234567",
        form_type="10-K",
        filing_date="2024-01-01",
        period="FY2023",
        source_url=None,
        raw_path="unused",
        parser_version="0.1.0",
        ingested_at=datetime.now(timezone.utc).isoformat(),
    )


def test_sections_and_paragraphs_are_extracted():
    blocks = parse_filing_html(_fixture_filing(), SAMPLE_HTML)
    sections = [b for b in blocks if b.block_type == "section"]
    paragraphs = [b for b in blocks if b.block_type == "paragraph"]

    assert any("Item 7" in (s.text or "") for s in sections)
    assert any("Revenue grew" in (p.text or "") for p in paragraphs)
    # the paragraph should be attributed to the Item 7 section path
    revenue_para = next(p for p in paragraphs if "Revenue grew" in (p.text or ""))
    assert "Item 7" in (revenue_para.section_path or "")


def test_table_cells_carry_header_paths_and_locators():
    blocks = parse_filing_html(_fixture_filing(), SAMPLE_HTML)
    cells = [b for b in blocks if b.block_type == "table_cell"]

    assert cells, "expected at least one table_cell block"
    revenue_2023 = next(
        c
        for c in cells
        if c.row_header_path == "Revenue" and c.col_header_path == "FY2023"
    )
    assert revenue_2023.text == "120"
    assert revenue_2023.dom_xpath  # every cell must carry a stable locator
    assert revenue_2023.table_id is not None
    assert revenue_2023.row_index == 1  # header row is index 0


def test_every_block_has_a_stable_dom_locator():
    blocks = parse_filing_html(_fixture_filing(), SAMPLE_HTML)
    for b in blocks:
        assert b.dom_xpath
        assert b.char_start is not None and b.char_end is not None
