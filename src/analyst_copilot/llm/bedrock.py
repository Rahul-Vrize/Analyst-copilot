"""Claude on Amazon Bedrock — the route that actually restores D4.

⚠️ WHY THIS EXISTS RATHER THAN THE FOUNDRY ONE. D4 requires two independent
model families at the generate→verify seam; both verifiers are currently
`gpt-5-mini`, which is self-verification wearing a costume. The planned fix was
Claude on Microsoft Foundry — and it is **not purchasable on our subscription**:
Claude bills there through Azure Marketplace, and Microsoft's docs exclude
"student, free trial, or startup credit-based accounts" and "sponsored
subscriptions that only use Azure credits". Adding a card does not redeem the
Azure credit either; the docs say the card is charged INSTEAD.

Bedrock has no such constraint. Claude is billed as ordinary AWS usage against
the account's credit balance, through AWS credentials — no separate Anthropic
account, no marketplace purchase. That makes it the one path to a genuine second
family here, which is why this adapter exists alongside `anthropic_foundry.py`.

⚠️ TRAPS THIS ENCODES:
  * use the **Mantle** client (`AnthropicBedrockMantle`) — the Messages-API
    endpoint. Plain `AnthropicBedrock` is the legacy `bedrock-runtime`
    InvokeModel path.
  * Bedrock model ids take an `anthropic.` PREFIX (`anthropic.claude-haiku-4-5`),
    unlike every other route. `deployment` is written verbatim, so put the
    prefixed id in `.env`; a missing prefix is a confusing 404, so it is
    detected and reported plainly.
  * the system prompt is a TOP-LEVEL `system=` parameter, not a message.
  * structured output is `output_config.format`, not `response_format`.
  * `AnthropicBedrock*` needs botocore for SigV4 signing — install
    `anthropic[bedrock]`, not bare `anthropic`.
  * **Model access must be granted per-model in the Bedrock console first.** An
    ungranted model returns AccessDenied, which reads like a credentials
    problem and is not one — so it is surfaced with the actual remedy.
"""

from __future__ import annotations

import json
from typing import Any

from ..config import ModelSettings, Settings
from .base import LLMError, LLMProvider, LLMRateLimited, LLMResponse, LLMTruncated


def _classify(exc: Exception, stage: str, model: str) -> LLMError:
    """Turn a boto/anthropic failure into the error the pipeline reasons about.

    The distinction matters: `LLMRateLimited` is retried with backoff, while a
    permissions failure must surface immediately — retrying it just burns the
    clock and, because verifiers fail closed, would show up as the system
    declining to answer rather than as a misconfiguration.
    """
    text = str(exc)
    lowered = text.lower()
    if "throttl" in lowered or "toomanyrequests" in lowered or "429" in lowered:
        return LLMRateLimited(f"{stage}: {text}")
    if "accessdenied" in lowered or "not authorized" in lowered or "forbidden" in lowered:
        return LLMError(
            f"{stage}: access denied for {model!r}. Bedrock requires model access to "
            f"be granted per model: Bedrock console -> Model access -> enable the "
            f"Anthropic models, and confirm AWS_REGION matches where you enabled "
            f"them. This is not a credentials problem. ({text})"
        )
    if (
        "nocredentials" in lowered
        or "unable to locate credentials" in lowered
        # The anthropic SDK's own wording, which differs from botocore's.
        or "could not resolve aws credentials" in lowered
    ):
        return LLMError(
            f"{stage}: no AWS credentials found. Set AWS_ACCESS_KEY_ID and "
            f"AWS_SECRET_ACCESS_KEY in .env (or AWS_PROFILE). ({text})"
        )
    if "signature" in lowered or "invalidclienttokenid" in lowered or "unrecognizedclient" in lowered:
        return LLMError(
            f"{stage}: AWS rejected the credentials — check the key pair was "
            f"copied whole and has not been rotated. ({text})"
        )
    if "could not connect" in lowered or "endpointconnectionerror" in lowered:
        return LLMError(
            f"{stage}: cannot reach Bedrock in this region — check AWS_REGION. ({text})"
        )
    if "validationexception" in lowered and "model identifier" in lowered:
        return LLMError(
            f"{stage}: {model!r} is not a valid Bedrock model id. Bedrock ids carry an "
            f"'anthropic.' prefix, e.g. 'anthropic.claude-haiku-4-5'. ({text})"
        )
    return LLMError(f"{stage}: {type(exc).__name__}: {text}")


class BedrockProvider(LLMProvider):
    """Implements the same `complete()` contract as every other adapter, so no
    stage knows which family answered it."""

    def __init__(self, settings: Settings, model: ModelSettings) -> None:
        try:
            from anthropic import AnthropicBedrockMantle
        except ImportError as exc:  # pragma: no cover
            raise LLMError(
                "provider 'bedrock' needs the Bedrock extra: "
                "pip install 'anthropic[bedrock]'"
            ) from exc

        if not settings.aws_region:
            raise LLMError("AWS_REGION is not set (required for Bedrock)")

        # Keys are optional on purpose: botocore resolves the standard chain
        # (env vars, shared profile, instance role) when they are absent, which
        # is how a grader on their own AWS account runs this unchanged.
        kwargs: dict[str, Any] = {"aws_region": settings.aws_region}
        if settings.aws_access_key_id and settings.aws_secret_access_key:
            kwargs["aws_access_key"] = settings.aws_access_key_id
            kwargs["aws_secret_key"] = settings.aws_secret_access_key
            if settings.aws_session_token:
                kwargs["aws_session_token"] = settings.aws_session_token
        elif settings.aws_profile:
            kwargs["aws_profile"] = settings.aws_profile

        self._client = AnthropicBedrockMantle(**kwargs)
        self._model = model

    def complete(
        self, *, system: str, user: str, schema: dict[str, Any], stage: str
    ) -> LLMResponse:
        m = self._model
        model_id = m.deployment
        kwargs: dict[str, Any] = {
            "model": model_id,
            # Top-level system parameter, NOT a {"role": "system"} message.
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "max_tokens": m.max_completion_tokens or 4096,
            "output_config": {"format": {"type": "json_schema", "schema": schema}},
        }
        if m.thinking:
            kwargs["thinking"] = {"type": m.thinking}

        try:
            resp = self._client.messages.create(**kwargs)
        except Exception as exc:
            raise _classify(exc, stage, model_id) from exc

        text = "".join(
            block.text for block in resp.content if getattr(block, "type", "") == "text"
        )
        stop_reason = getattr(resp, "stop_reason", "") or ""
        if not text:
            # Its own type: an empty completion must never be mistaken for a
            # refusal, or a budget problem becomes a silent abstention.
            raise LLMTruncated(
                f"{stage}: empty content (stop_reason={stop_reason!r}, "
                f"max_tokens={kwargs['max_tokens']})"
            )

        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise LLMError(
                f"{stage}: structured output was not valid JSON: {exc}"
            ) from exc

        usage = getattr(resp, "usage", None)
        return LLMResponse(
            data=data,
            raw=text,
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            model=model_id,
            stage=stage,
            finish_reason=stop_reason,
        )
