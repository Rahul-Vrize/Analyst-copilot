#!/usr/bin/env python
"""Score the system against practice-questions.jsonl using the exact
rubric from the problem statement:

    correct answer, correct location   -> +1
    "Not found in this filing"         ->  0
    correct answer, wrong location     ->  0
    confidently wrong answer           -> -1

STATUS: the practice-questions.jsonl schema was not available when this was
scaffolded (analyst-copilot-data.zip ships separately). `_load_questions`
guesses at common field names (question/answer/evidence/page/filing) and
should be adjusted to match the real file on first run — print one raw
record to check before trusting the aggregate score. Answer-correctness
and location-correctness matching below are intentionally simple
(normalized substring / numeric match) and should be tightened once real
question/answer pairs are visible; an approximate matcher that's too lax
will overstate the score, which is worse than an eval that under-scores.

Usage:
    python evaluation/run_eval.py data/practice/practice-questions.jsonl
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from analyst_copilot.abstention.policy import NOT_FOUND  # noqa: E402
from analyst_copilot.llm.client import LLMClient  # noqa: E402
from analyst_copilot.service import answer_question  # noqa: E402
from analyst_copilot.storage.db import get_sqlite_conn  # noqa: E402


@dataclass(frozen=True)
class PracticeQuestion:
    filing_id: str
    question: str
    expected_answer: str
    expected_page: int | None
    expected_passage: str | None


def _load_questions(path: Path) -> list[PracticeQuestion]:
    questions = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            questions.append(
                PracticeQuestion(
                    filing_id=rec.get("filing_id") or rec.get("filing") or rec.get("document") or "",
                    question=rec.get("question") or rec.get("q") or "",
                    expected_answer=str(rec.get("answer", "")),
                    expected_page=rec.get("page"),
                    expected_passage=rec.get("passage") or rec.get("evidence"),
                )
            )
    return questions


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9.]", "", text.lower())


def _answer_matches(expected: str, actual: str) -> bool:
    if not expected:
        return False
    return _normalize(expected) in _normalize(actual)


def _location_matches(pq: PracticeQuestion, evidence_pages: list[int | None]) -> bool:
    if pq.expected_page is None:
        return True  # can't check location without a gold page; don't penalize
    return pq.expected_page in [p for p in evidence_pages if p is not None]


def score_all(questions: list[PracticeQuestion], use_llm: bool = True) -> None:
    conn = get_sqlite_conn()
    llm = LLMClient() if use_llm else None

    total = 0
    correct_answer = 0
    correct_location = 0
    abstained = 0
    wrong = 0

    for pq in questions:
        if not pq.filing_id:
            continue
        result = answer_question(conn, pq.filing_id, pq.question, llm=llm)
        total += 1

        if result.abstained or result.answer_text.strip() == NOT_FOUND:
            abstained += 1
            score = 0
        else:
            ans_ok = _answer_matches(pq.expected_answer, result.answer_text)
            loc_ok = _location_matches(pq, [e.rendered_page for e in result.evidence])
            if ans_ok and loc_ok:
                correct_answer += 1
                correct_location += 1
                score = 1
            elif ans_ok and not loc_ok:
                correct_answer += 1
                score = 0
            else:
                wrong += 1
                score = -1

        print(f"[{score:+d}] {pq.question[:80]!r} -> {result.answer_text[:80]!r}")

    print("-" * 72)
    print(f"total questions run   : {total}")
    print(f"correct + located (+1): {correct_location}")
    print(f"correct, wrong loc (0): {correct_answer - correct_location}")
    print(f"abstained (0)         : {abstained}")
    print(f"confidently wrong (-1): {wrong}")
    net = correct_location - wrong
    print(f"net score             : {net}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("questions_path")
    parser.add_argument("--no-llm", action="store_true", help="Skip LLM synthesis; score raw evidence text")
    args = parser.parse_args()

    questions = _load_questions(Path(args.questions_path))
    if not questions:
        print("No questions loaded — check the file path and schema assumptions above.")
        return
    score_all(questions, use_llm=not args.no_llm)


if __name__ == "__main__":
    main()
