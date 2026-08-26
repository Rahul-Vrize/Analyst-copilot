"""Builds an AnswerPlan from a raw question. Phase 4 (design doc build
order). The first planner can be a prompt-based classifier with
deterministic fallbacks — it should choose lookup / table calculation /
narrative synthesis / bounded multi-hop, never freely invent a tool
sequence.

STATUS: stub. Currently only fills question_type via the keyword router;
does not yet decompose multi-hop questions into evidence slots or call the
LLM. Wire this up once retrieval/table_lookup and fact_lookup are backed by
real data (blocked on XBRL extraction for fact_lookup).
"""

from __future__ import annotations

from analyst_copilot.reasoning.answer_plan import AnswerPlan
from analyst_copilot.retrieval.router import route_question


def build_plan(question: str) -> AnswerPlan:
    path = route_question(question)
    return AnswerPlan(question=question, question_type=path.value)
