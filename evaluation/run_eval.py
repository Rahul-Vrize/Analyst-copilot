#!/usr/bin/env python
"""Score the system against practice-questions.jsonl using the exact
rubric from the problem statement:

    correct answer, correct location   -> +1
    "Not found in this filing"         ->  0
    correct answer, wrong location     ->  0
    confidently wrong answer           -> -1

The practice set is FinanceBench-format (confirmed against the real file):
each record has `doc_name` (matches a filename stem under data/filings/,
not our content-hash filing_id — resolved via
storage.repository.get_filing_by_doc_name), `question`, `answer`, and an
`evidence` list of {evidence_text, evidence_page_num, ...}. A question can
carry more than one gold evidence page (e.g. a synthesis question spanning
the income statement and balance sheet); any one of them counts as a
correct location.

Answer matching is numeric-aware because gold answers are frequently
unit-converted from the raw filing value (e.g. answer "$8.70" billion vs.
the filing's raw cell "8,738" [millions]) — an exact-string or substring
match would wrongly fail those. See `_numbers_match`.

Usage:
    python evaluation/run_eval.py data/practice-questions.jsonl
    python evaluation/run_eval.py data/practice-questions.jsonl --limit 20
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
from analyst_copilot.storage import repository  # noqa: E402
from analyst_copilot.storage.db import get_sqlite_conn  # noqa: E402

_NUMBER_RE = re.compile(r"\(?-?\$?\s*-?\d[\d,]*(?:\.\d+)?\)?%?")


@dataclass(frozen=True)
class PracticeQuestion:
    doc_name: str
    question: str
    expected_answer: str
    expected_pages: list[int]


def _load_questions(path: Path) -> list[PracticeQuestion]:
    questions = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            evidence = rec.get("evidence") or []
            pages = sorted(
                {e["evidence_page_num"] for e in evidence if e.get("evidence_page_num") is not None}
            )
            questions.append(
                PracticeQuestion(
                    doc_name=rec["doc_name"],
                    question=rec["question"],
                    expected_answer=str(rec.get("answer", "")),
                    expected_pages=pages,
                )
            )
    return questions


def _extract_numbers(text: str) -> list[float]:
    numbers = []
    for raw in _NUMBER_RE.findall(text):
        cleaned = raw.replace("$", "").replace(",", "").replace("%", "").strip()
        negative = cleaned.startswith("(") and cleaned.endswith(")")
        cleaned = cleaned.strip("()")
        if not cleaned or cleaned == "-":
            continue
        try:
            value = float(cleaned)
        except ValueError:
            continue
        numbers.append(-value if negative else value)
    return numbers


# Common unit-scale confusions between a gold answer (often stated in a
# rounded/converted unit) and the filing's raw displayed value.
_SCALE_FACTORS = (1, 1_000, 1_000_000, 1 / 1_000, 1 / 1_000_000)


def _numbers_match(expected: float, actual: float, rel_tol: float = 0.015) -> bool:
    if expected == 0:
        return abs(actual) < 1e-6
    for candidate in (actual, -actual):
        # sign-agnostic: filings report outflows (capex, buybacks, ...) as
        # negative/parenthesized; gold answers often restate them as a
        # positive magnitude. Comparing both signs treats that as a match
        # rather than a wrong answer.
        for factor in _SCALE_FACTORS:
            if abs(candidate * factor - expected) <= abs(expected) * rel_tol:
                return True
    return False


def _answer_matches(expected: str, actual: str) -> bool:
    if not expected:
        return False
    expected_numbers = _extract_numbers(expected)
    if expected_numbers:
        actual_numbers = _extract_numbers(actual)
        return any(
            _numbers_match(e, a) for e in expected_numbers for a in actual_numbers
        )
    # Non-numeric (yes/no, qualitative) gold answer: fall back to a loose
    # keyword overlap rather than exact substring, since phrasing varies.
    expected_words = {w for w in re.findall(r"[a-z]{3,}", expected.lower())}
    actual_words = {w for w in re.findall(r"[a-z]{3,}", actual.lower())}
    if not expected_words:
        return False
    overlap = len(expected_words & actual_words) / len(expected_words)
    return overlap >= 0.5


def _location_matches(pq: PracticeQuestion, evidence_pages: list[int | None]) -> bool:
    if not pq.expected_pages:
        return True  # no gold page to check against; don't penalize
    predicted = {p for p in evidence_pages if p is not None}
    return bool(predicted & set(pq.expected_pages))


def score_all(questions: list[PracticeQuestion], use_llm: bool = True, limit: int | None = None) -> None:
    conn = get_sqlite_conn()
    llm = LLMClient() if use_llm else None

    total = 0
    correct_location = 0
    correct_wrong_location = 0
    abstained = 0
    wrong = 0
    skipped_not_ingested: set[str] = set()

    for pq in questions[:limit] if limit else questions:
        filing_row = repository.get_filing_by_doc_name(conn, pq.doc_name)
        if filing_row is None:
            skipped_not_ingested.add(pq.doc_name)
            continue

        total += 1
        result = answer_question(conn, filing_row["filing_id"], pq.question, llm=llm)

        if result.abstained or result.answer_text.strip() == NOT_FOUND:
            abstained += 1
            score = 0
        else:
            ans_ok = _answer_matches(pq.expected_answer, result.answer_text)
            loc_ok = _location_matches(pq, [e.rendered_page for e in result.evidence])
            if ans_ok and loc_ok:
                correct_location += 1
                score = 1
            elif ans_ok and not loc_ok:
                correct_wrong_location += 1
                score = 0
            else:
                wrong += 1
                score = -1

        print(
            f"[{score:+d}] ({pq.doc_name}) {pq.question[:70]!r}\n"
            f"      gold={pq.expected_answer[:60]!r} pages={pq.expected_pages}\n"
            f"      got ={result.answer_text[:60]!r} "
            f"pages={[e.rendered_page for e in result.evidence]}"
        )

    print("-" * 72)
    if skipped_not_ingested:
        print(
            f"skipped (filing not ingested): {len(skipped_not_ingested)} "
            f"e.g. {sorted(skipped_not_ingested)[:5]}"
        )
    print(f"total questions scored : {total}")
    print(f"correct + located (+1) : {correct_location}")
    print(f"correct, wrong loc (0) : {correct_wrong_location}")
    print(f"abstained (0)          : {abstained}")
    print(f"confidently wrong (-1) : {wrong}")
    net = correct_location - wrong
    print(f"net score              : {net}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("questions_path")
    parser.add_argument("--no-llm", action="store_true", help="Skip LLM synthesis; score raw evidence text")
    parser.add_argument("--limit", type=int, default=None, help="Only score the first N questions")
    args = parser.parse_args()

    questions = _load_questions(Path(args.questions_path))
    if not questions:
        print("No questions loaded — check the file path and schema assumptions above.")
        return
    score_all(questions, use_llm=not args.no_llm, limit=args.limit)


if __name__ == "__main__":
    main()
