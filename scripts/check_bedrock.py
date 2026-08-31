#!/usr/bin/env python
"""Confirm Claude on Bedrock works before spending an eval run on it.

    python scripts/check_bedrock.py

Costs a few hundred tokens. Every failure mode it can hit reports the actual
remedy, because the two most common ones — model access not granted, and a
missing `anthropic.` prefix — both look like credential problems and are not.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from analyst_copilot.config import load_settings          # noqa: E402
from analyst_copilot.llm.base import LLMError             # noqa: E402
from analyst_copilot.llm.registry import get_provider     # noqa: E402

SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["VALID", "INVALID"]},
        "reasoning": {"type": "string"},
    },
    "required": ["verdict", "reasoning"],
    "additionalProperties": False,
}


def main() -> int:
    settings = load_settings()
    model = settings.model("verifier_b")
    print(f"verifier_b  provider={model.provider}  deployment={model.deployment}")
    print(f"region      {settings.aws_region}")
    print(f"credentials {'keys in .env' if settings.aws_access_key_id else 'default chain'}\n")

    if model.provider not in ("bedrock", "bedrock_converse"):
        print("verifier_b is not pointed at Bedrock — set VERIFIER_B_PROVIDER to "
              "'bedrock_converse' (Amazon Nova) or 'bedrock' (Anthropic Messages API)")
        return 1
    if model.provider == "bedrock" and not model.deployment.startswith("anthropic."):
        print(f"⚠️  {model.deployment!r} is missing the 'anthropic.' prefix that the "
              f"Anthropic-on-Bedrock route requires")
        return 1

    try:
        response = get_provider(settings, "verifier_b").complete(
            system=(
                "You are checking another system's answer. Reply VALID or "
                "INVALID with one sentence of reasoning."
            ),
            user=(
                "QUESTION: What was FY2018 capex?\n"
                "PROPOSED ANSWER: $1,577 million in FY2018\n"
                'QUOTES: "Purchases of property, plant and equipment (PP&E) (1,577)"'
            ),
            schema=SCHEMA,
            stage="verifier_b",
        )
    except LLMError as exc:
        print(f"FAILED\n  {exc}")
        return 1

    print("PASS")
    print(f"  verdict   {response.data.get('verdict')}")
    print(f"  reasoning {str(response.data.get('reasoning'))[:120]}")
    print(f"  tokens    {response.input_tokens} in / {response.output_tokens} out")
    print("\nA genuine second family is now verifying the extractor's answers.")
    print("Next: set `verifier_b_adversarial: false` in config.yaml — refutation "
          "framing was a SUBSTITUTE for independence, not an addition to it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
