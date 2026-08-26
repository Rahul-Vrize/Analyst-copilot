"""Thin wrapper around the SQLite FTS5 lexical search in storage.repository.
Kept as its own module so dense/hybrid fusion (design doc: reciprocal-rank
fusion + optional reranking) has a clear seam to slot into later, without
callers caring whether ranking is pure BM25 or hybrid."""

from __future__ import annotations

import re
import sqlite3

from analyst_copilot.storage import repository

_FTS5_SPECIAL = re.compile(r'[":^*()]')

# A natural-language question is mostly function words the filing text will
# never contain ("why", "did", "what"). Dropping them keeps the OR query
# below from being dominated by noise terms while still letting FTS5's
# bm25() ranking (not required-term conjunction) decide relevance.
_STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "what", "which", "who", "whom", "why", "how", "when", "where",
    "did", "does", "do", "has", "have", "had", "of", "in", "on", "for",
    "to", "and", "or", "as", "at", "by", "with", "this", "that", "it",
}


def _sanitize_fts5_query(text: str) -> str:
    """FTS5 query syntax treats quotes/operators specially; strip them,
    drop stopwords, and quote each remaining token. Terms are joined with
    OR (not the implicit AND of space-separated terms) so a question
    retrieves the best-ranked partial match instead of requiring every
    word — including function words absent from the filing — to appear."""
    tokens = [
        t for t in _FTS5_SPECIAL.sub(" ", text).split() if t and t.lower() not in _STOPWORDS
    ]
    return " OR ".join(f'"{t}"' for t in tokens)


def search_narrative(
    conn: sqlite3.Connection, filing_id: str, question: str, limit: int = 8
) -> list[sqlite3.Row]:
    query = _sanitize_fts5_query(question)
    if not query:
        return []
    return repository.bm25_search(conn, filing_id, query, limit=limit)
