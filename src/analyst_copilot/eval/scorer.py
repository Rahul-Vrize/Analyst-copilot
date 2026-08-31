"""The rubric scorer - one correctness predicate per answer shape (§20.3).

    correct answer, correct location   -> +1
    "Not found in this filing."        ->  0
    correct answer, WRONG location     ->  0
    answered and answer wrong          -> -1

The asymmetry is the whole game: a confident wrong answer is worth -1 while an
honest refusal is worth 0, so the system is optimising PRECISION-CALIBRATED
SELECTIVE ANSWERING, not accuracy. Two abstentions are worth more than one
wrong answer.

An LLM judge is required for the phrase and multi-sentence shapes. It is
injected, never constructed here, so the whole scorer runs offline for the
numeric and yes/no shapes and in unit tests (§24.5).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Protocol

from .gold import GoldQuestion, tokens
from .shapes import AnswerShape

# Scale words that appear in gold answers but not in the filing's own figures:
# gold "$8.70 billion" against a filing cell of "8,738" (millions).
_SCALES = {
    "thousand": Decimal(10) ** 3, "thousands": Decimal(10) ** 3, "k": Decimal(10) ** 3,
    "million": Decimal(10) ** 6, "millions": Decimal(10) ** 6, "mm": Decimal(10) ** 6,
    "billion": Decimal(10) ** 9, "billions": Decimal(10) ** 9, "bn": Decimal(10) ** 9,
    "trillion": Decimal(10) ** 12,
}
_NUM_WITH_SCALE = re.compile(
    r"([-+(]?\s*\$?\s*\d[\d,]*(?:\.\d+)?\s*\)?)\s*"
    r"(thousands?|millions?|billions?|trillions?|k|mm|bn)?",
    re.I,
)
_YES_NO_TOKEN = re.compile(r"^\s*(yes|no)\b", re.I)


class Judge(Protocol):
    """An LLM judge for the two free-text shapes. Injected, never constructed."""

    def equivalent(self, question: str, gold: str, candidate: str) -> bool: ...

    def contradicted(self, question: str, gold: str, candidate: str) -> bool: ...


@dataclass
class Citation:
    doc_id: str
    page_seq: int
    quote: str = ""


@dataclass
class SystemAnswer:
    """What the pipeline produced. `abstained` is the honest-refusal path."""

    text: str = ""
    abstained: bool = False
    clarified: bool = False
    citations: list[Citation] = field(default_factory=list)


@dataclass
class ScoredResult:
    qid: str
    shape: AnswerShape
    score: int
    answer_correct: bool
    location_correct: bool
    abstained: bool
    note: str = ""


# ---------------------------------------------------------------------------
# Numeric comparison
# ---------------------------------------------------------------------------
def parse_number(text: str) -> Decimal | None:
    """Parse the leading figure of an answer, applying any scale word.

    Gold answers are frequently unit-converted from the filing's own value
    ("$8.70 billion" vs a cell reading "8,738" in millions), so an exact-string
    or substring match would wrongly fail them.
    """
    m = _NUM_WITH_SCALE.search(text or "")
    if not m:
        return None
    raw, scale = m.group(1), (m.group(2) or "").lower()
    negative = "(" in raw
    raw = raw.replace("(", "").replace(")", "").replace("$", "")
    raw = raw.replace(",", "").replace("+", "").strip()
    if raw.startswith("-"):
        negative, raw = True, raw[1:].strip()
    if not raw:
        return None
    try:
        value = Decimal(raw)
    except InvalidOperation:
        return None
    if scale in _SCALES:
        value *= _SCALES[scale]
    return -value if negative else value


def numbers_match(gold: str, candidate: str, tolerance: Decimal) -> bool:
    """Compare within a relative tolerance, after normalising scale.

    Percentages are compared on their face value (12.4% vs 0.124 would be a
    presentation difference, but treating them as equal risks accepting a
    genuinely wrong figure, so it is deliberately NOT done here).
    """
    g, c = parse_number(gold), parse_number(candidate)
    if g is None or c is None:
        return False
    if g == c:
        return True
    if g == 0:
        return abs(c) <= tolerance
    if abs(g - c) / abs(g) <= tolerance:
        return True
    # A pure scale mismatch (8.70 vs 8,700) is still the same fact if one side
    # simply omitted its unit word.
    for factor in (Decimal(10) ** 3, Decimal(10) ** 6, Decimal(10) ** 9):
        for a, b in ((g * factor, c), (g, c * factor)):
            if b != 0 and abs(a - b) / abs(b) <= tolerance:
                return True
    return False


# ---------------------------------------------------------------------------
# Location
# ---------------------------------------------------------------------------
def location_ok(
    question: GoldQuestion,
    citations: list[Citation],
    pages_by_seq: dict[int, str],
    *,
    containment: float,
    page_slack: int,
) -> bool:
    """A citation is correct when it lands on a gold page, or adjacent to one.

    Two routes, either sufficient:
      1. the cited page's text contains the gold evidence (containment >= 0.6)
      2. the cited page_seq is within +/-1 of a mapped gold page

    Route 2 exists because a financial statement routinely spans a page seam,
    and citing the facing page of a two-page statement is not a location error a
    human would call wrong.
    """
    if not citations:
        return False
    cited_docs = {c.doc_id for c in citations}
    if question.doc_name not in cited_docs:
        return False

    for citation in citations:
        if citation.doc_id != question.doc_name:
            continue
        if any(abs(citation.page_seq - g) <= page_slack for g in question.gold_page_seqs):
            return True
        page_text = pages_by_seq.get(citation.page_seq, "")
        page = tokens(page_text)
        if not page:
            continue
        for gold_text in question.evidence_texts:
            gold = tokens(gold_text)
            if gold and len(gold & page) / len(gold) >= containment:
                return True
    return False


def _all_operands_located(
    question: GoldQuestion,
    citations: list[Citation],
    pages_by_seq: dict[int, str],
    *,
    containment: float,
    page_slack: int,
) -> bool:
    """Every gold evidence passage must be cited, not just one.

    A yes/no judgement rests on all of its operands, and a multi-sentence answer
    needs a citation per claim - "a paragraph with one citation fails".
    """
    if not citations:
        return False
    for gold_text in question.evidence_texts:
        gold = tokens(gold_text)
        if not gold:
            continue
        satisfied = False
        for citation in citations:
            if citation.doc_id != question.doc_name:
                continue
            page = tokens(pages_by_seq.get(citation.page_seq, ""))
            if page and len(gold & page) / len(gold) >= containment:
                satisfied = True
                break
            if any(
                abs(citation.page_seq - g) <= page_slack
                for g in question.gold_page_seqs
            ):
                satisfied = True
                break
        if not satisfied:
            return False
    return True


# ---------------------------------------------------------------------------
# The scorer
# ---------------------------------------------------------------------------
class RubricScorer:
    def __init__(
        self,
        *,
        numeric_tolerance: Decimal,
        location_containment: float,
        page_seq_slack: int,
        judge: Judge | None = None,
    ) -> None:
        self.numeric_tolerance = numeric_tolerance
        self.location_containment = location_containment
        self.page_seq_slack = page_seq_slack
        self.judge = judge

    def answer_correct(self, question: GoldQuestion, answer: SystemAnswer) -> bool:
        gold, candidate = question.answer, answer.text
        shape = question.shape

        if shape is AnswerShape.NUMERIC:
            return numbers_match(gold, candidate, self.numeric_tolerance)

        if shape is AnswerShape.YES_NO:
            g = _YES_NO_TOKEN.match(gold)
            c = _YES_NO_TOKEN.match(candidate)
            if not g or not c:
                return False
            if g.group(1).lower() != c.group(1).lower():
                return False
            # The leading token agreeing is necessary but not sufficient: the
            # justification must not be contradicted by the gold reasoning.
            if self.judge is None:
                return True
            return not self.judge.contradicted(question.question, gold, candidate)

        # PHRASE and MULTI_SENTENCE both need a judge. Without one the harness
        # must not silently mark them correct - that would flatter the system,
        # which is the exact failure §19.5 warns about.
        if self.judge is None:
            return False
        return self.judge.equivalent(question.question, gold, candidate)

    def location_correct(
        self,
        question: GoldQuestion,
        answer: SystemAnswer,
        pages_by_seq: dict[int, str],
    ) -> bool:
        if question.shape in (AnswerShape.YES_NO, AnswerShape.MULTI_SENTENCE):
            return _all_operands_located(
                question,
                answer.citations,
                pages_by_seq,
                containment=self.location_containment,
                page_slack=self.page_seq_slack,
            )
        return location_ok(
            question,
            answer.citations,
            pages_by_seq,
            containment=self.location_containment,
            page_slack=self.page_seq_slack,
        )

    def score(
        self,
        question: GoldQuestion,
        answer: SystemAnswer,
        pages_by_seq: dict[int, str],
    ) -> ScoredResult:
        if answer.abstained or answer.clarified:
            return ScoredResult(
                qid=question.qid,
                shape=question.shape,
                score=0,
                answer_correct=False,
                location_correct=False,
                abstained=True,
                note="abstained" if answer.abstained else "clarified",
            )

        ok_answer = self.answer_correct(question, answer)
        if not ok_answer:
            # Answered and wrong. This is the only -1, and it is why the
            # abstention gate is the component that earns the score.
            return ScoredResult(
                question.qid, question.shape, -1, False, False, False, "wrong answer"
            )

        ok_location = self.location_correct(question, answer, pages_by_seq)
        return ScoredResult(
            qid=question.qid,
            shape=question.shape,
            score=1 if ok_location else 0,
            answer_correct=True,
            location_correct=ok_location,
            abstained=False,
            note="" if ok_location else "right answer, wrong location",
        )
