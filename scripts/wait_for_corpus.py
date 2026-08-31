#!/usr/bin/env python
"""Exit 0 once the corpus is fully ingested and intact, else exit 1.

    until python scripts/wait_for_corpus.py --expect 78; do sleep 30; done

⚠️ CHECKS THE DATABASE, NOT THE INGEST LOG. The ingest can succeed and still
fail to print its summary — the setup connection sits idle for the whole run and
Azure drops idle connections, so `corpus_stats` at the end can throw after every
filing landed correctly. Waiting on log text would then wait forever for a run
that had already finished.

"Intact" deliberately means `ready` AND `page_count > 0`: a filing whose content
was deleted and not re-inserted still has a `filings` row, so counting rows alone
would call a damaged corpus complete. That exact state was hit twice.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from analyst_copilot.config import load_settings          # noqa: E402
from analyst_copilot.storage.db import connect            # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--expect", type=int, default=78)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    settings = load_settings()
    try:
        with connect(settings.database_url) as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT count(*) FILTER (WHERE ingest_status = 'ready'
                                             AND COALESCE(page_count, 0) > 0) AS ok,
                          count(*) AS total,
                          (SELECT count(*) FROM pages) AS pages
                     FROM filings"""
            )
            row = cur.fetchone()
    except Exception as exc:
        if not args.quiet:
            print(f"not ready: {type(exc).__name__}: {str(exc)[:80]}")
        return 1

    if not args.quiet:
        print(f"  {row['ok']}/{args.expect} filings intact, {row['pages']:,} pages")
    return 0 if row["ok"] >= args.expect else 1


if __name__ == "__main__":
    raise SystemExit(main())
