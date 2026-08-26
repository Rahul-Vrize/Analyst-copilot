"""Analyst Copilot: evidence-first question answering over SEC filings.

Pipeline (see /Building a Trustworthy SEC Filing Analyst Copilot.md for the
full design rationale):

    EDGAR HTML + Inline XBRL
        -> typed DOM tree + fact ledger        (analyst_copilot.ingestion)
        -> SQLite (FTS5) + DuckDB storage       (analyst_copilot.storage)
        -> filing-local routed hybrid retrieval (analyst_copilot.retrieval)
        -> evidence-slot plan + deterministic
           SQL/Decimal calculation             (analyst_copilot.reasoning)
        -> independent evidence/formula check   (analyst_copilot.verification)
        -> claim-level evidence ledger          (analyst_copilot.evidence)
        -> calibrated exact refusal             (analyst_copilot.abstention)
"""

__version__ = "0.1.0"
