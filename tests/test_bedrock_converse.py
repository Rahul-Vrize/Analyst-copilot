"""Amazon Nova on Bedrock — the second family that is actually reachable.

⚠️ THE ROUTE WAS BLOCKED TWICE BEFORE THIS ONE, and both blocks were the same
one wearing different words: Claude is Azure-Marketplace-subscribed on Foundry
and AWS-Marketplace-subscribed on Bedrock, and a credit-only account can
complete neither. ✅ MEASURED on Bedrock:

    "Model access is denied due to INVALID_PAYMENT_INSTRUMENT: A valid payment
     instrument must be provided. Your AWS Marketplace subscription for this
     model cannot be completed at this time."

Amazon's own models are first-party and carry no such requirement, and what D4
actually asks for is a family that is not the one under test.
"""

from __future__ import annotations

import json

import pytest

from analyst_copilot.llm.base import LLMError, LLMRateLimited
from analyst_copilot.llm.bedrock_converse import _classify, strip_fences
from analyst_copilot.llm.registry import _ADAPTERS

MODEL = "us.amazon.nova-pro-v1:0"


def test_provider_is_registered():
    assert "bedrock_converse" in _ADAPTERS


# ---------------------------------------------------------------------------
# Fence stripping — measured, not hypothetical
# ---------------------------------------------------------------------------
def test_the_exact_shape_nova_returned():
    """✅ MEASURED response: '```json\n{\n  "verdict": "VALID"\n}\n```'.
    Parsed raw this is not valid JSON, and the stage would treat a perfectly
    good answer as a model failure — which, for a fail-closed verifier, becomes
    a silent abstention."""
    raw = '```json\n{\n  "verdict": "VALID"\n}\n```'
    assert json.loads(strip_fences(raw)) == {"verdict": "VALID"}


@pytest.mark.parametrize("raw,expected", [
    ('```json\n{"a": 1}\n```', {"a": 1}),
    ('```\n{"a": 1}\n```', {"a": 1}),
    ('{"a": 1}', {"a": 1}),
    ('  \n {"a": 1}  \n ', {"a": 1}),
    ('Here is the answer:\n{"a": 1}\nHope that helps.', {"a": 1}),
])
def test_fenced_prefixed_and_bare_json_all_parse(raw, expected):
    assert json.loads(strip_fences(raw)) == expected


def test_nested_objects_survive_the_outermost_brace_fallback():
    raw = 'Result:\n{"verdict": "VALID", "detail": {"page": 61}}\ndone'
    assert json.loads(strip_fences(raw)) == {
        "verdict": "VALID", "detail": {"page": 61}
    }


def test_unparseable_text_is_returned_for_the_caller_to_report():
    """Better a clear JSON error naming the text than a silent empty dict."""
    assert strip_fences("no json here") == "no json here"
    assert strip_fences("") == ""


# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------
def test_the_marketplace_block_names_the_first_party_alternative():
    """The failure that cost us Claude on both clouds. The message must point at
    the way out, not just restate the error."""
    err = _classify(
        Exception("Model access is denied due to INVALID_PAYMENT_INSTRUMENT: A valid "
                  "payment instrument must be provided."),
        "verifier_b", "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    )
    assert isinstance(err, LLMError) and not isinstance(err, LLMRateLimited)
    assert "amazon.nova" in str(err)


def test_throttling_stays_retryable():
    assert isinstance(
        _classify(Exception("ThrottlingException: Rate exceeded"), "verifier_b", MODEL),
        LLMRateLimited,
    )


def test_a_bad_model_id_names_the_region_prefix():
    err = _classify(
        Exception("ValidationException: The provided model identifier is invalid"),
        "verifier_b", "amazon.nova-pro",
    )
    assert "us.amazon.nova-pro-v1:0" in str(err)


def test_missing_credentials_names_the_env_vars():
    err = _classify(
        Exception("Could not resolve AWS credentials from session"), "verifier_b", MODEL
    )
    assert "AWS_ACCESS_KEY_ID" in str(err)
