"""Thin wrapper around the SQLite FTS5 lexical search in storage.repository.
Kept as its own module so dense/hybrid fusion (design doc: reciprocal-rank
fusion + optional reranking) has a clear seam to slot into later, without
callers caring whether ranking is pure BM25 or hybrid."""

from __future__ import annotations

import re
import sqlite3

from analyst_copilot.retrieval import aliases
from analyst_copilot.storage import repository

_FTS5_SPECIAL = re.compile(r'[":^*()]')

# A natural-language question is mostly function words the filing text will
# never contain ("why", "did", "what"). Dropping them keeps the OR query
# below from being dominated by noise terms while still letting FTS5's
# bm25() ranking (not required-term conjunction) decide relevance.
#
# Also dropped: generic analyst-prompt scaffolding ("assume", "based on",
# "relying on the details shown") and generic filing/table boilerplate
# ("amount", "location", "total", "millions") that repeats near-verbatim
# across thousands of unrelated rows/paragraphs in a real filing and would
# otherwise out-rank the one row that actually carries the answer. Column
# weighting (storage/repository.py) does most of the real work; this list
# just keeps the OR query from being diluted by terms that match almost
# every block regardless of relevance.
_STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "what", "which", "who", "whom", "why", "how", "when", "where",
    "did", "does", "do", "has", "have", "had", "of", "in", "on", "for",
    "to", "and", "or", "as", "at", "by", "with", "this", "that", "it",
    "you", "your", "we", "i", "if", "so", "please", "give", "answer",
    "question", "following", "assume", "based", "primarily", "relying",
    "details", "shown", "using", "answering", "response", "amount",
    "amounts", "location", "total", "value", "millions", "thousands",
    "dollars", "usd", "fiscal", "year", "years", "company", "please",
    "can", "could", "would", "should", "also", "than", "then",
}


def _sanitize_fts5_query(text: str) -> str:
    """FTS5 query syntax treats quotes/operators specially; strip them,
    drop stopwords, and quote each remaining token. Terms are joined with
    OR (not the implicit AND of space-separated terms) so a question
    retrieves the best-ranked partial match instead of requiring every
    word — including function words absent from the filing — to appear.

    Also ORs in filing line-item synonyms for any recognized metric phrase
    (see retrieval.aliases) as quoted multi-word phrases, since analyst
    terminology and filing wording routinely share zero tokens (e.g.
    "capital expenditure" vs. the filing's "Purchases of property, plant
    and equipment")."""
    tokens = [
        t for t in _FTS5_SPECIAL.sub(" ", text).split() if t and t.lower() not in _STOPWORDS
    ]
    clauses = [f'"{t}"' for t in tokens]
    for synonym in aliases.expand(text):
        words = aliases.tokenize_phrase(_FTS5_SPECIAL.sub(" ", synonym))
        if words:
            clauses.append('"' + " ".join(words) + '"')  # phrase (adjacency) match
    return " OR ".join(clauses)


def _norm_for_match(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", text.lower())).strip()


def _rerank_exact_alias_hits(hits: list[sqlite3.Row], question: str) -> list[sqlite3.Row]:
    """Raw bm25() can still let a short accidental match (e.g. a segment
    subtotal cell literally labeled "Capital Spending") outrank the actual
    target row, because per-field length normalization rewards brevity
    regardless of whether the match is the *specific* line item asked
    about. Deterministically promote any hit whose row/col header contains
    a full expanded alias phrase — a stronger, more specific signal than
    bm25's per-token score — ahead of everything else, preserving bm25
    order within each group. This is a cheap stand-in for the doc's
    "cross-encoder or LLM reranker" (Phase 3) that costs no extra call."""
    phrases = [_norm_for_match(s) for s in aliases.expand(question)]
    if not phrases:
        return hits

    def is_exact_hit(row: sqlite3.Row) -> bool:
        haystack = _norm_for_match(
            " ".join(filter(None, [row["row_header_path"], row["col_header_path"]]))
        )
        return any(phrase and phrase in haystack for phrase in phrases)

    def has_digit(row: sqlite3.Row) -> bool:
        return bool(re.search(r"\d", row["text"] or ""))

    boosted = [h for h in hits if is_exact_hit(h)]
    rest = [h for h in hits if not is_exact_hit(h)]
    # Within the boosted group, a row/label cell (its own text just repeats
    # the header, e.g. "Purchases of property...") ties in bm25 score with
    # its sibling value cells on the same row. Break that tie toward cells
    # that actually carry a value — the label cell alone isn't an answer.
    boosted.sort(key=lambda h: 0 if has_digit(h) else 1)
    return boosted + rest


def search_narrative(
    conn: sqlite3.Connection,
    filing_id: str,
    question: str,
    limit: int = 8,
    block_types: tuple[str, ...] | None = None,
) -> list[sqlite3.Row]:
    query = _sanitize_fts5_query(question)
    if not query:
        return []
    # Pull a larger candidate pool than requested so the exact-alias rerank
    # below has hits to promote even if bm25 buried them past `limit`.
    pool = repository.bm25_search(
        conn, filing_id, query, limit=max(limit * 4, 20), block_types=block_types
    )
    return _rerank_exact_alias_hits(pool, question)[:limit]
