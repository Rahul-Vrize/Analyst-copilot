"""Deterministic checks run before any model-based verification (design
doc, "Verification and Evidence"): slot completeness, evidence-accession
membership, period/dimension agreement, unit compatibility, formula
recomputation, and location existence.

STATUS: slot completeness and location existence are implemented against
the current schema. Period/dimension/unit checks need the fact/cell ledger
(Phase 2) to compare requested vs. evidence period reliably; formula
recomputation needs reasoning.calculator wiring into the candidate record.
Each check returns a CheckResult so the verifier can report *which* check
failed rather than an opaque rejection.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from analyst_copilot.evidence.ledger import AnswerCandidate
from analyst_copilot.reasoning.answer_plan import AnswerPlan
from analyst_copilot.storage import repository


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    detail: str = ""


def check_slots_complete(plan: AnswerPlan, candidate: AnswerCandidate) -> CheckResult:
    unresolved = [s.name for s in plan.required_evidence_slots if not s.resolved]
    if unresolved:
        return CheckResult("slots_complete", False, f"unresolved slots: {unresolved}")
    return CheckResult("slots_complete", True)


def check_claims_have_evidence(candidate: AnswerCandidate) -> CheckResult:
    for claim in candidate.claims:
        if not claim.evidence_ids:
            return CheckResult(
                "claims_have_evidence", False, f"claim without evidence_ids: {claim.text!r}"
            )
    return CheckResult("claims_have_evidence", True)


def check_evidence_locations_exist(
    conn: sqlite3.Connection, filing_id: str, candidate: AnswerCandidate
) -> CheckResult:
    """Every cited location must belong to the requested accession/filing
    and actually exist in the stored source (design doc: "every cited
    location exists in the stored source hash")."""
    for claim in candidate.claims:
        for block_id in claim.evidence_ids:
            block = repository.get_block(conn, block_id)
            if block is None:
                return CheckResult(
                    "evidence_locations_exist", False, f"missing block: {block_id}"
                )
            if block["filing_id"] != filing_id:
                return CheckResult(
                    "evidence_locations_exist",
                    False,
                    f"block {block_id} belongs to a different filing",
                )
    return CheckResult("evidence_locations_exist", True)


def run_deterministic_checks(
    conn: sqlite3.Connection, filing_id: str, plan: AnswerPlan, candidate: AnswerCandidate
) -> list[CheckResult]:
    return [
        check_slots_complete(plan, candidate),
        check_claims_have_evidence(candidate),
        check_evidence_locations_exist(conn, filing_id, candidate),
    ]
