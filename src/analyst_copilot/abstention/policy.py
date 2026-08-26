"""Calibrated abstention (design doc, "Abstention and Evaluation"). Scoring
rubric: correct answer + correct location = +1, "Not found in this filing"
= 0, correct answer + wrong location = 0, confidently wrong = -1. A system
that guesses finishes below zero; a system that always abstains finishes at
exactly zero — the goal is to beat zero, not to avoid it.

STATUS: `should_abstain` currently implements only the deterministic gate
(missing slots / failed checks). The calibrated threshold tuned on a
held-out negative set (design doc: "Calibrate on synthetic and hard
negatives") is Phase 5 — see evaluation/negatives/.
"""

from __future__ import annotations

from analyst_copilot.evidence.ledger import AnswerCandidate

NOT_FOUND = "Not found in this filing."


def should_abstain(candidate: AnswerCandidate) -> bool:
    if candidate.status == "unsupported":
        return True
    if candidate.missing_slots:
        return True
    if not candidate.claims:
        return True
    if any(not claim.evidence_ids for claim in candidate.claims):
        return True
    return False
