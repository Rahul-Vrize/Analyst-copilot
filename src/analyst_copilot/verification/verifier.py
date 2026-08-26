"""Independent verification stage: deterministic checks first, model checks
second (design doc, "Verification and Evidence"). The verifier can reject a
proposed answer; it can never manufacture replacement evidence.

STATUS: deterministic gate is wired up. The model-based verifier ("Does
each claim follow from its cited evidence? Is the answer using a
definition not present in the filing?") is Phase 5 — see llm/client.py for
where that call should go once it's added.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from analyst_copilot.evidence.ledger import AnswerCandidate
from analyst_copilot.reasoning.answer_plan import AnswerPlan
from analyst_copilot.verification.deterministic_checks import (
    CheckResult,
    run_deterministic_checks,
)


@dataclass(frozen=True)
class VerifierResult:
    supported: bool
    checks: list[CheckResult]

    @property
    def failed_checks(self) -> list[CheckResult]:
        return [c for c in self.checks if not c.passed]


def verify(
    conn: sqlite3.Connection, filing_id: str, plan: AnswerPlan, candidate: AnswerCandidate
) -> VerifierResult:
    checks = run_deterministic_checks(conn, filing_id, plan, candidate)
    return VerifierResult(supported=all(c.passed for c in checks), checks=checks)
