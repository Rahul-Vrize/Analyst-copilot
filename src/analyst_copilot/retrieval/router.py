"""Query router: classify a question into one of the four retrieval paths
from the design doc ("Retrieval: Hybrid, Routed, and Filing-Local").

Phase 1/3 implementation is keyword-heuristic, not an LLM call, so routing
stays cheap and auditable. Replace/augment with a prompt-based classifier in
Phase 4 (analyst_copilot.reasoning.planner) once AnswerPlan construction
needs it — but keep a deterministic fallback, per the doc's guidance that
routing decisions should not by themselves cost an LLM call where metadata
already resolves them.
"""

from __future__ import annotations

import re
from enum import Enum

_CALC_WORDS = re.compile(
    r"\b(change|growth|increase|decrease|cagr|percent|ratio|margin|"
    r"compare|difference|total|sum|average)\b",
    re.IGNORECASE,
)
_TABLE_WORDS = re.compile(
    r"\b(table|revenue|segment|balance sheet|income statement|cash flow|"
    r"capex|capital expenditure)\b",
    re.IGNORECASE,
)
_MULTIHOP_WORDS = re.compile(
    r"\b(excluding|adjusted for|driven by|because|versus|relative to|"
    r"contributed)\b",
    re.IGNORECASE,
)


class QueryPath(str, Enum):
    FACT_LOOKUP = "fact_lookup"
    TABLE_LOOKUP = "table_lookup"
    NARRATIVE_LOOKUP = "narrative_lookup"
    MULTI_HOP = "multi_hop"


def route_question(question: str) -> QueryPath:
    if _MULTIHOP_WORDS.search(question):
        return QueryPath.MULTI_HOP
    if _CALC_WORDS.search(question) and _TABLE_WORDS.search(question):
        return QueryPath.TABLE_LOOKUP
    if _TABLE_WORDS.search(question):
        return QueryPath.FACT_LOOKUP
    return QueryPath.NARRATIVE_LOOKUP
