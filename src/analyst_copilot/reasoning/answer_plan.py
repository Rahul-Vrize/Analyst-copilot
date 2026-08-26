"""The AnswerPlan schema (design doc, "Reasoning and Calculations"). A
question is compiled into this structure before any retrieval happens; the
system cannot answer until every required evidence slot is either
supported or explicitly marked missing."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

QuestionType = Literal[
    "fact_lookup", "table_lookup", "narrative_lookup", "multi_hop", "calculation"
]


class EvidenceSlot(BaseModel):
    name: str  # e.g. "capex_fy2018"
    description: str  # e.g. "capex FY2018"
    period: str | None = None
    dimensions: dict[str, str] = Field(default_factory=dict)
    unit: str | None = None
    resolved: bool = False
    evidence_ids: list[str] = Field(default_factory=list)


class AnswerPlan(BaseModel):
    question: str
    question_type: QuestionType
    requested_periods: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    dimensions: dict[str, str] = Field(default_factory=dict)
    required_evidence_slots: list[EvidenceSlot] = Field(default_factory=list)
    operations: list[str] = Field(default_factory=list)  # e.g. ["subtract", "percent_change"]
    output_format: Literal["number", "text", "table", "boolean"] = "text"
    abstention_conditions: list[str] = Field(default_factory=list)

    def all_slots_resolved(self) -> bool:
        return all(slot.resolved for slot in self.required_evidence_slots)
