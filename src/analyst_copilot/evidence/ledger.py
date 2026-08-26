"""The evidence-first contract (design doc, "Verification and Evidence").
The final generator receives only the question, the AnswerPlan, selected
evidence, formulas, and the verifier result — never asked to add facts from
general knowledge or silently fill missing slots."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from analyst_copilot.ingestion.locator import EvidenceLocator


class Claim(BaseModel):
    text: str
    evidence_ids: list[str]
    formula: str | None = None
    value: str | None = None


class AnswerCandidate(BaseModel):
    status: Literal["supported", "unsupported"]
    claims: list[Claim] = Field(default_factory=list)
    missing_slots: list[str] = Field(default_factory=list)
    locations: list[EvidenceLocator] = Field(default_factory=list)
