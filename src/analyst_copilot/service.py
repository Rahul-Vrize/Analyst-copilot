"""Wires the pipeline together end-to-end for a single question against a
single filing. This is the MVP vertical slice: narrative BM25 retrieval ->
evidence-first candidate -> deterministic verification -> abstain or
synthesize an answer strictly from the retrieved passage.

It intentionally does NOT yet cover: XBRL fact lookup, table calculation,
multi-hop planning, or model-based verification — those are the Phase
2-5 items called out throughout the package docstrings. Both the
Streamlit app and the evaluation harness call through here, so upgrading
one phase (e.g. wiring up find_facts) benefits both immediately.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from analyst_copilot.abstention.policy import NOT_FOUND, should_abstain
from analyst_copilot.evidence.ledger import AnswerCandidate, Claim
from analyst_copilot.ingestion.locator import EvidenceLocator
from analyst_copilot.llm.client import LLMClient
from analyst_copilot.reasoning.planner import build_plan
from analyst_copilot.retrieval.bm25 import search_narrative
from analyst_copilot.storage import repository
from analyst_copilot.verification.verifier import verify

# Below this BM25 rank threshold (bm25() returns *more negative = better*
# in SQLite's FTS5), we treat the top hit as too weak to answer from.
# Not calibrated yet — see design doc "Abstention Requires Deliberate Data".
_MIN_RELEVANCE_RANK = -0.5


@dataclass(frozen=True)
class AnswerResult:
    answer_text: str
    abstained: bool
    evidence: list[EvidenceLocator]
    verifier_passed_checks: list[str]
    verifier_failed_checks: list[str]


def answer_question(
    conn: sqlite3.Connection,
    filing_id: str,
    question: str,
    llm: LLMClient | None = None,
) -> AnswerResult:
    plan = build_plan(question)
    hits = search_narrative(conn, filing_id, question, limit=5)

    if not hits or hits[0]["rank"] > _MIN_RELEVANCE_RANK:
        candidate = AnswerCandidate(status="unsupported", missing_slots=["no_relevant_evidence"])
    else:
        top = hits[0]
        filing_row = repository.get_filing(conn, filing_id)
        locator = EvidenceLocator(
            accession=filing_row["accession"] if filing_row else None,
            filing_url=filing_row["source_url"] if filing_row else None,
            section_path=top["section_path"],
            dom_id_or_xpath=top["dom_xpath"],
            char_start=top["char_start"],
            char_end=top["char_end"],
            table_id=top["table_id"],
            row=top["row_index"],
            column=top["col_index"],
            rendered_page=top["rendered_page"],
        )
        candidate = AnswerCandidate(
            status="supported",
            claims=[Claim(text=top["text"] or "", evidence_ids=[top["block_id"]])],
            locations=[locator],
        )

    result = verify(conn, filing_id, plan, candidate)

    if should_abstain(candidate) or not result.supported:
        return AnswerResult(
            answer_text=NOT_FOUND,
            abstained=True,
            evidence=[],
            verifier_passed_checks=[c.name for c in result.checks if c.passed],
            verifier_failed_checks=[c.name for c in result.failed_checks],
        )

    evidence_text = candidate.claims[0].text
    answer_text = evidence_text
    if llm is not None:
        try:
            answer_text = llm.complete(
                prompt=(
                    f"Question: {question}\n\n"
                    f"Evidence passage (the ONLY source you may use):\n{evidence_text}\n\n"
                    "Answer the question using only this passage. If the passage does not "
                    "actually answer it, reply exactly: Not found in this filing."
                ),
                system=(
                    "You are a financial filing analyst assistant. Never use outside "
                    "knowledge. Never state a number not present in the supplied passage."
                ),
                # Reasoning-model providers (e.g. Bedrock's gpt-oss) spend part of this
                # budget on a hidden reasoning block before the visible answer text.
                max_tokens=1024,
            )
        except Exception:  # noqa: BLE001 - LLM synthesis is best-effort; fall back to raw evidence
            answer_text = evidence_text

    return AnswerResult(
        answer_text=answer_text,
        abstained=False,
        evidence=candidate.locations,
        verifier_passed_checks=[c.name for c in result.checks if c.passed],
        verifier_failed_checks=[c.name for c in result.failed_checks],
    )
