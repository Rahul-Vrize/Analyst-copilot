"""Claude on AWS Bedrock — the route that actually restores D4 independence.

⚠️ WHY THIS MATTERS. Both verifiers are `gpt-5-mini`, which is self-verification.
The planned fix — Claude on Microsoft Foundry — is NOT purchasable on a
credit-only Azure subscription (Marketplace billing excludes free-trial and
credit-based accounts). Bedrock bills Claude as ordinary AWS usage against the
account's credits, so it is the one path to a genuine second family here.

Errors are classified rather than lumped together because verifiers FAIL CLOSED:
an unretryable permissions problem that got retried, or got recorded as a
rejection, would show up as the system declining to answer rather than as a
misconfiguration — the exact artifact that makes an abstention rate meaningless.
"""

from __future__ import annotations

import pytest

from analyst_copilot.llm.base import LLMError, LLMRateLimited
from analyst_copilot.llm.bedrock import _classify
from analyst_copilot.llm.registry import _ADAPTERS

MODEL = "anthropic.claude-haiku-4-5"


def test_bedrock_is_a_registered_provider():
    """§22.7 promises a family swap is one adapter file plus one registry entry."""
    assert "bedrock" in _ADAPTERS


# ---------------------------------------------------------------------------
# Retryable vs terminal
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("message", [
    "ThrottlingException: Rate exceeded",
    "TooManyRequestsException",
    "An error occurred (429) when calling the Converse operation",
])
def test_throttling_is_retryable(message):
    assert isinstance(_classify(Exception(message), "verifier_b", MODEL), LLMRateLimited)


@pytest.mark.parametrize("message", [
    "AccessDeniedException: You don't have access to the model with the specified model ID",
    "User is not authorized to perform bedrock:InvokeModel",
])
def test_missing_model_access_is_terminal_and_explains_the_actual_remedy(message):
    """The most common Bedrock first-run failure. It reads like bad credentials
    and is not — model access is granted PER MODEL in the console."""
    err = _classify(Exception(message), "verifier_b", MODEL)
    assert isinstance(err, LLMError) and not isinstance(err, LLMRateLimited)
    assert "Model access" in str(err)
    assert "not a credentials problem" in str(err)


def test_a_missing_model_id_prefix_is_named_explicitly():
    """Bedrock ids carry an `anthropic.` prefix that no other route uses."""
    err = _classify(
        Exception("ValidationException: The provided model identifier is invalid"),
        "verifier_b", "claude-haiku-4-5",
    )
    assert "anthropic." in str(err)


def test_a_wrong_region_is_named_as_a_region_problem():
    err = _classify(
        Exception("EndpointConnectionError: Could not connect to the endpoint URL"),
        "verifier_b", MODEL,
    )
    assert "AWS_REGION" in str(err)


def test_an_unknown_failure_still_surfaces_with_its_type():
    err = _classify(ValueError("something unexpected"), "verifier_b", MODEL)
    assert isinstance(err, LLMError) and not isinstance(err, LLMRateLimited)
    assert "ValueError" in str(err)


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------
def test_region_is_required_and_says_so():
    from dataclasses import replace
    from analyst_copilot.config import load_settings
    from analyst_copilot.llm.bedrock import BedrockProvider

    settings = replace(load_settings(), aws_region=None)
    model = settings.model("verifier_b")
    with pytest.raises(LLMError, match="AWS_REGION"):
        BedrockProvider(settings, replace(model, provider="bedrock", deployment=MODEL))


@pytest.mark.parametrize("message", [
    "NoCredentialsError: Unable to locate credentials",
    "RuntimeError: Could not resolve AWS credentials from session",
])
def test_missing_credentials_names_the_env_vars(message):
    """MEASURED: the anthropic SDK words this differently from botocore, and the
    first version of the classifier matched only botocore's phrasing — so the
    most likely first-run failure fell through to a generic message."""
    err = _classify(Exception(message), "verifier_b", MODEL)
    assert "AWS_ACCESS_KEY_ID" in str(err)


@pytest.mark.parametrize("message", [
    "InvalidClientTokenId: The security token included in the request is invalid",
    "SignatureDoesNotMatch: The request signature we calculated does not match",
])
def test_bad_credentials_are_distinguished_from_missing_ones(message):
    err = _classify(Exception(message), "verifier_b", MODEL)
    assert "rejected the credentials" in str(err)
