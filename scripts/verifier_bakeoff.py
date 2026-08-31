#!/usr/bin/env python
"""Pick the verifier from labelled cases, not from a full eval run.

    python scripts/verifier_bakeoff.py

⚠️ WHY THIS EXISTS. Choosing a verifier by running the whole pipeline costs ~30
minutes and ~2M tokens per candidate, and most of that spend measures retrieval
and extraction — stages the verifier choice does not touch. Every past eval run
already recorded, per question: the question, the proposed answer, the quotes
that supported it, and whether it was RIGHT or WRONG. Replaying those through a
candidate verifier costs a few dozen small calls and measures the one property
that matters.

⚠️ THE TWO ERRORS ARE NOT SYMMETRIC, AND THE RUBRIC SAYS SO.
  * accepting a WRONG answer  -> -1  (and -2 against the refusal it displaced)
  * rejecting a RIGHT answer  ->  0  (a lost +1)
So a verifier is scored primarily on how many wrong answers it CATCHES, with
over-rejection reported beside it — never one without the other.

✅ MEASURED, and the reason this script was written: swapping verifier B from
gpt-5-mini to Amazon Nova Pro gave genuine two-family independence (D4) and made
the system WORSE — 60% false-answer rate on the first batch, twice, with and
without adversarial framing. Independence and competence are separate axes.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from analyst_copilot.config import load_settings              # noqa: E402
from analyst_copilot.llm import schemas                       # noqa: E402
from analyst_copilot.llm.base import LLMError                 # noqa: E402
from analyst_copilot.llm.registry import get_provider, load_prompt  # noqa: E402

# provider, deployment, adversarial
CANDIDATES = [
    ("azure_openai",     "gpt-5-mini",                          True),
    ("azure_openai",     "gpt-5-mini",                          False),
    ("bedrock_converse", "us.amazon.nova-pro-v1:0",             True),
    ("bedrock_converse", "us.meta.llama3-3-70b-instruct-v1:0",  True),
    ("bedrock_converse", "openai.gpt-oss-120b-1:0",             True),
]


def load_cases(paths: list[Path]) -> list[dict]:
    """Labelled verifier inputs, newest file wins on a repeated qid.

    Only decided outcomes are usable: a +1 is an answer that SHOULD be accepted,
    a -1 an answer that SHOULD be rejected. Abstentions carry no label — the
    verifier never saw an answer — and a 0 from "right answer, wrong location"
    is a LOCATION failure, which is not the verifier's job.
    """
    cases: dict[str, dict] = {}
    for path in paths:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("abstained") or row["score"] == 0 or not row.get("citations"):
                continue
            cases[row["qid"]] = {
                "qid": row["qid"],
                "shape": row["shape"],
                "question": row["question"],
                "answer": row["got"],
                "gold": row["gold"],
                "should_accept": row["score"] == 1,
                "quotes": "\n".join(
                    f"[{c['doc_id']} p.{c['page_seq']}] {c['quote']}"
                    for c in row["citations"]
                ),
            }
    return sorted(cases.values(), key=lambda c: (c["should_accept"], c["qid"]))


def judge(settings, provider: str, deployment: str, adversarial: bool, case: dict):
    model = replace(
        settings.model("verifier_b"), provider=provider, deployment=deployment,
        # OpenAI-only knobs; the azure adapter degrades automatically, but not
        # sending them to a non-OpenAI endpoint keeps the comparison clean.
        reasoning_effort=None if provider != "azure_openai" else "medium",
        verbosity=None if provider != "azure_openai" else "low",
    )
    tuned = replace(settings, models={**settings.models, "verifier_b": model})
    prompt = load_prompt("verify_adversarial" if adversarial else "verify")
    schema = schemas.VERIFY_ADVERSARIAL if adversarial else schemas.VERIFY
    ok_token = "SUPPORTED" if adversarial else "VALID"
    user = (
        f"QUESTION:\n{case['question']}\n\nPROPOSED ANSWER:\n{case['answer']}\n\n"
        f"QUOTES:\n{case['quotes']}"
    )
    try:
        data = get_provider(tuned, "verifier_b").complete(
            system=prompt, user=user, schema=schema, stage="verifier_b"
        ).data
    except LLMError as exc:
        return None, str(exc)[:70]
    verdict = (data.get("verdict") or "").upper()
    return verdict == ok_token, str(data.get("reasoning") or "")[:70]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=".cache/verifier_bakeoff.json")
    args = ap.parse_args()

    settings = load_settings()
    cases = load_cases([
        ROOT / ".cache" / name for name in (
            "ab_A_questions.jsonl", "ab_A2_questions.jsonl",
            "adversarial_off_5q.jsonl", "clean_baseline.jsonl",
        )
    ])
    wrong = [c for c in cases if not c["should_accept"]]
    right = [c for c in cases if c["should_accept"]]
    print(f"{len(cases)} labelled cases: {len(wrong)} WRONG (must reject), "
          f"{len(right)} RIGHT (must accept)\n")
    if not wrong:
        print("no wrong-answer cases available — nothing to discriminate on")
        return 1

    results = {}
    for provider, deployment, adversarial in CANDIDATES:
        label = f"{deployment}{' +adv' if adversarial else ' neutral'}"
        caught = accepted = errors = 0
        detail = []
        for case in cases:
            verdict, note = judge(settings, provider, deployment, adversarial, case)
            if verdict is None:
                errors += 1
                continue
            if case["should_accept"] and verdict:
                accepted += 1
            if not case["should_accept"] and not verdict:
                caught += 1
            detail.append({"qid": case["qid"], "should_accept": case["should_accept"],
                           "verdict": verdict, "note": note})
        results[label] = {"caught": caught, "of_wrong": len(wrong),
                          "accepted": accepted, "of_right": len(right),
                          "errors": errors, "detail": detail}
        print(f"  {label:<44} catches {caught}/{len(wrong)} wrong   "
              f"keeps {accepted}/{len(right)} right"
              + (f"   ({errors} errors)" if errors else ""))

    print("\n" + "=" * 78)
    print("A verifier that catches every wrong answer by rejecting everything is")
    print("worthless: read both columns. Under this rubric a missed wrong answer")
    print("costs 2 points against the refusal it displaced; an over-rejection")
    print("costs 1. Prefer catching, but not at any price.")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\ndetail -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
