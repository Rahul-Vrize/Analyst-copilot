# Approach Note

*(One page. Update this as the system evolves — don't write it all at the
end.)*

## What we tried

- Started from a literature-backed architecture review
  (`Building a Trustworthy SEC Filing Analyst Copilot.md`) rather than
  guessing at a RAG design: filing-local structure-aware evidence over
  generic chunk-and-embed.
- Built the ingestion → typed block tree → SQLite/FTS5 storage → BM25
  retrieval → evidence-first verification → calibrated abstention pipeline
  as separate, independently testable stages instead of one prompt.

## What we measured

*(Fill in once `evaluation/run_eval.py` runs against the real
practice-questions.jsonl: net score, false-answer rate on negatives,
retrieval recall, ingestion time per filing.)*

## What we kept

- Two-database split (SQLite for the typed tree + FTS5, DuckDB for
  fact/cell numeric analytics) — cheap, inspectable, no server to run.
- Evidence-first contract: the generator only proposes a claim after
  retrieval/calculation produce evidence IDs; the verifier can reject but
  never fabricate evidence.

## What we threw away

*(Update as decisions get revisited — e.g. if BM25-only relevance
thresholding proves too blunt, or if the keyword-based router
under/over-triggers multi-hop.)*
