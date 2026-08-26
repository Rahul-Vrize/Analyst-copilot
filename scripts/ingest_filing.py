#!/usr/bin/env python
"""CLI: ingest one local filing HTML file (or a batch directory of them).

Usage:
    python scripts/ingest_filing.py path/to/filing.htm --company "Acme Inc" --form-type 10-K
    python scripts/ingest_filing.py data/filings/ --batch
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from analyst_copilot.ingestion.pipeline import ingest_filing  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", help="Path to a filing HTML file, or a directory with --batch")
    parser.add_argument("--batch", action="store_true", help="Treat `path` as a directory of *.htm/*.html files")
    parser.add_argument("--company")
    parser.add_argument("--form-type")
    parser.add_argument("--filing-date")
    parser.add_argument("--period")
    args = parser.parse_args()

    targets = (
        sorted(Path(args.path).glob("*.htm")) + sorted(Path(args.path).glob("*.html"))
        if args.batch
        else [Path(args.path)]
    )
    if not targets:
        print(f"No .htm/.html files found under {args.path}")
        return

    for target in targets:
        record, block_count, elapsed = ingest_filing(
            str(target),
            company=args.company,
            form_type=args.form_type,
            filing_date=args.filing_date,
            period=args.period,
        )
        print(
            f"[{elapsed:6.1f}s] {target.name}: filing_id={record.filing_id[:12]} "
            f"blocks={block_count}"
        )


if __name__ == "__main__":
    main()
