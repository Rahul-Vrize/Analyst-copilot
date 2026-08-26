#!/usr/bin/env python
"""Phase 1 sanity check (design doc, "Hackathon Build Order", Phase 1):
"Create a small inspection script that prints a table cell, its header
path, its XBRL metadata, and its exact locator." XBRL metadata is not yet
available (Phase 2), so this currently prints table cells and their
locators only.

Usage:
    python scripts/inspect_evidence.py <filing_id_prefix> [--limit 20]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from analyst_copilot.storage.db import get_sqlite_conn  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("filing_id_prefix")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--block-type", default="table_cell")
    args = parser.parse_args()

    conn = get_sqlite_conn()
    rows = conn.execute(
        """
        SELECT * FROM blocks
        WHERE filing_id LIKE ? AND block_type = ?
        LIMIT ?
        """,
        (f"{args.filing_id_prefix}%", args.block_type, args.limit),
    ).fetchall()

    if not rows:
        print("No matching blocks. Has this filing been ingested?")
        return

    for row in rows:
        print("-" * 72)
        print(f"block_id     : {row['block_id']}")
        print(f"section_path : {row['section_path']}")
        print(f"row_header   : {row['row_header_path']}")
        print(f"col_header   : {row['col_header_path']}")
        print(f"text         : {row['text']}")
        print(f"dom_xpath    : {row['dom_xpath']}")
        print(f"table/row/col: {row['table_id']} / {row['row_index']} / {row['col_index']}")
        print(f"char_span    : [{row['char_start']}, {row['char_end']}]")


if __name__ == "__main__":
    main()
