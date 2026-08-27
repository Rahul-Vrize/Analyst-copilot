"""Metric-alias query expansion. Pure lexical retrieval cannot bridge
analyst phrasing to filing line-item wording when they share no words at
all — e.g. gold evidence for "capital expenditure" is literally "Purchases
of property, plant and equipment (PP&E)" in the cash flow statement, a
FinanceBench-verified case with zero lexical overlap. The design doc's
`find_facts(metric_aliases, ...)` concept anticipates exactly this; this is
a lightweight stand-in for that until real XBRL tag aliasing
(ingestion/xbrl_parser.py) is wired up.

Deliberately small and hand-curated rather than exhaustive — only the
metrics that recur constantly across analyst questions over financial
statements. Extend as new gaps are observed, not preemptively.
"""

from __future__ import annotations

import re

# question phrase (lowercase, matched as substring) -> extra search terms
# to OR into the query (the filing's own line-item wording).
_ALIASES: dict[str, list[str]] = {
    "capital expenditure": ["purchases of property plant and equipment", "capital spending", "additions to property"],
    "capex": ["purchases of property plant and equipment", "capital expenditures"],
    "revenue": ["net sales", "net revenues", "total revenue", "net revenue"],
    "net sales": ["revenue", "net revenues"],
    "ppne": ["property plant and equipment net"],
    "ppe": ["property plant and equipment"],
    "property, plant and equipment": ["property plant equipment net"],
    "cogs": ["cost of sales", "cost of goods sold", "cost of revenue"],
    "cost of goods sold": ["cost of sales", "cost of revenue"],
    "net income": ["net earnings", "net income attributable"],
    "total assets": ["total assets"],
    "total liabilities": ["total liabilities"],
    "cash and cash equivalents": ["cash and equivalents"],
    "r&d": ["research and development"],
    "sg&a": ["selling general and administrative"],
    "dividend": ["dividends paid", "dividends declared"],
    "eps": ["earnings per share"],
    "quick ratio": ["cash", "receivables", "current liabilities"],
    "current ratio": ["total current assets", "total current liabilities"],
    "operating margin": ["operating income", "net sales"],
    "gross margin": ["gross profit", "cost of sales"],
    "fixed asset turnover": ["property plant and equipment net", "net sales"],
    "accounts receivable": ["accounts receivable"],
    "accounts payable": ["accounts payable"],
    "long-term debt": ["long term debt"],
    "shareholders equity": ["stockholders equity", "shareholders equity"],
    "free cash flow": ["cash flow from operations", "capital expenditures"],
}


def expand(question: str) -> list[str]:
    """Return extra lexical terms to search for, based on any known metric
    phrase detected in the question. Does not modify the original query —
    callers OR these in alongside it."""
    q = question.lower()
    extra: list[str] = []
    for phrase, synonyms in _ALIASES.items():
        if phrase in q:
            extra.extend(synonyms)
    return extra


def tokenize_phrase(phrase: str) -> list[str]:
    return [t for t in re.split(r"\s+", phrase.strip()) if t]
