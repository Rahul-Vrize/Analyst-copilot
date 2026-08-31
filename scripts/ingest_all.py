#!/usr/bin/env python
"""Ingest the corpus into PostgreSQL (IMPLEMENTATION_PLAN §19.2 task 1.7).

Works on any directory of SEC filings, not just the practice corpus - that is
the point of §25: a judge's unseen filing must ingest by the same path.

    python scripts/ingest_all.py                  # everything, skip unchanged
    python scripts/ingest_all.py --limit 3        # smoke run
    python scripts/ingest_all.py --only 3M_2018_10K
    python scripts/ingest_all.py --force          # re-parse even if unchanged
"""

from __future__ import annotations

import argparse
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from analyst_copilot.config import load_settings                      # noqa: E402
from analyst_copilot.ingest.pipeline import ingest_filing             # noqa: E402
from analyst_copilot.query.router import load_aliases                 # noqa: E402
from analyst_copilot.storage import repository as repo                # noqa: E402
from analyst_copilot.storage.db import connect, healthcheck           # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ingest SEC filings")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--only", default="")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dir", default="", help="override FILINGS_DIR")
    args = parser.parse_args(argv)

    settings = load_settings()
    filings_dir = Path(args.dir) if args.dir else settings.filings_dir

    paths = sorted(filings_dir.glob("*.htm"))
    if args.only:
        paths = [p for p in paths if p.stem == args.only]
    if args.limit:
        paths = paths[: args.limit]
    if not paths:
        print(f"no filings found in {filings_dir}", file=sys.stderr)
        return 1

    with connect(settings.database_url) as conn:
        health = healthcheck(conn)
        if not health["migrations"]:
            print(
                "schema not applied - run:\n"
                '  psql "$DATABASE_URL" -f migrations/001_init.sql',
                file=sys.stderr,
            )
            return 1
        print(f"db ok: {health['n_tables']} tables, embedding {health['embedding_type']}")

        n = repo.seed_company_aliases(
            conn, load_aliases(settings.data_dir / "company_aliases.yaml")
        )
        conn.commit()
        print(f"company aliases seeded: {n} rows")

        totals = dict(pages=0, blocks=0, tables=0, data=0, aligned=0, cells=0, sections=0)
        failed: list[tuple[str, str]] = []
        started = time.time()

        for i, path in enumerate(paths, 1):
            t0 = time.time()
            try:
                r = ingest_filing(conn, path, settings, force=args.force)
            except Exception as exc:  # one bad filing must not stop the corpus
                conn.rollback()
                failed.append((path.stem, f"{type(exc).__name__}: {exc}"))
                print(f"[{i}/{len(paths)}] {path.stem:44s} FAILED {exc}")
                traceback.print_exc(limit=3)
                continue

            if r.skipped:
                print(f"[{i}/{len(paths)}] {path.stem:44s} unchanged, skipped")
                continue

            totals["pages"] += r.pages
            totals["blocks"] += r.blocks
            totals["tables"] += r.tables
            totals["data"] += r.data_tables
            totals["aligned"] += r.aligned_tables
            totals["cells"] += r.cells
            totals["sections"] += r.sections
            print(
                f"[{i}/{len(paths)}] {path.stem:44s} "
                f"pages={r.pages:4d} sections={r.sections:3d} blocks={r.blocks:5d} "
                f"tables={r.tables:4d} aligned={r.aligned_tables:3d} "
                f"cells={r.cells:5d}  {time.time() - t0:5.1f}s"
            )

        elapsed = time.time() - started
        print("\n" + "=" * 72)
        print(f"ingested {len(paths) - len(failed)}/{len(paths)} filings in {elapsed:.1f}s")
        for k, v in totals.items():
            print(f"  {k:10s} {v:,}")
        if totals["data"]:
            print(f"  alignment_ok {100 * totals['aligned'] / totals['data']:.1f}% of data tables")
        if failed:
            print(f"\nFAILED ({len(failed)}):")
            for doc_id, err in failed:
                print(f"  {doc_id}: {err}")

        print("\ncorpus now holds:")
        for table, count in repo.corpus_stats(conn).items():
            print(f"  {table:16s} {count:,}")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
