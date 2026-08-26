"""Deterministic lookup API over the XBRL fact ledger (design doc Phase 2:
`find_facts(metric_aliases, period, dimensions, unit)`).

STATUS: depends on analyst_copilot.ingestion.xbrl_parser, which is not yet
implemented. Until then this raises, so callers fail loudly instead of
silently returning nothing (which would look like "not found in this
filing" for the wrong reason).
"""

from __future__ import annotations

import duckdb


def find_facts(
    conn: duckdb.DuckDBPyConnection,
    filing_id: str,
    metric_aliases: list[str],
    period: str | None = None,
    dimensions: dict | None = None,
    unit: str | None = None,
) -> list[dict]:
    raise NotImplementedError(
        "find_facts requires the fact_ledger to be populated by "
        "ingestion.xbrl_parser (Phase 2), which is not yet implemented."
    )
