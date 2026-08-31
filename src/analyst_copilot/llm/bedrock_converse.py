"""Amazon Nova on Bedrock — the second model family that actually works here.

⚠️ THE ROUTE TO A SECOND FAMILY WAS BLOCKED TWICE BEFORE THIS ONE.
D4 requires two independent model families at the generate→verify seam; both
verifiers are `gpt-5-mini`, which is self-verification wearing a costume.

  1. Claude on Microsoft Foundry — bills through Azure Marketplace, which
     Microsoft's docs exclude for "free trial" and credit-only subscriptions.
  2. Claude on AWS Bedrock — ✅ MEASURED to fail the same way, despite the
     common claim that Bedrock is plain AWS usage:
         "Model access is denied due to INVALID_PAYMENT_INSTRUMENT: A valid
          payment instrument must be provided. Your AWS Marketplace
          subscription for this model cannot be completed at this time."
     Anthropic models on Bedrock are Marketplace-subscribed too. One call
     succeeded before the entitlement check caught up; three retries over two
     minutes then failed identically, so that success was a race, not a path.

  3. AMAZON NOVA IS AWS FIRST-PARTY — no Marketplace subscription, no payment
     instrument. ✅ MEASURED working on this account: `us.amazon.nova-pro-v1:0`
     answered in 2.0 s. Amazon is a genuinely different family from OpenAI, so
     D4's independence requirement is satisfied by the property that matters -
     it is not the model under test checking its own work.

⚠️ TRAPS THIS ENCODES:
  * this is the Bedrock **converse** API via boto3, NOT the Anthropic Messages
    API — different shapes entirely, which is why it is a separate adapter from
    `bedrock.py` rather than a branch inside it.
  * the system prompt is `system=[{"text": ...}]`, a LIST of blocks.
  * ✅ MEASURED: Nova wraps structured output in ```json fences. Parsing the raw
    text as JSON fails; every stage in this system parses structured output, so
    the fences are stripped before parsing rather than being prompted away.
  * inference-profile ids (`us.` prefix) are more reliably available than bare
    model ids, and were measured returning better-formed JSON.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ..config import ModelSettings, Settings
from .base import LLMError, LLMProvider, LLMRateLimited, LLMResponse, LLMTruncated

# ```json { ... } ```  /  ``` { ... } ```
_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.S | re.I)


def strip_fences(text: str) -> str:
    """Return the JSON body from a possibly fenced response.

    ✅ MEASURED: Nova returns '```json\\n{\\n  "verdict": "VALID"\\n}\\n```'.
    Left alone that is not valid JSON, and the stage that parses it would treat
    a perfectly good response as a model failure — which, for a verifier that
    fails closed, becomes a silent abstention.
    """
    body = (text or "").strip()
    match = _FENCE.match(body)
    if match:
        return match.group(1).strip()
    # Some responses lead with prose and then the object; take the outermost
    # braces rather than giving up.
    start, end = body.find("{"), body.rfind("}")
    if start != -1 and end > start:
        return body[start:end + 1]
    return body


def _classify(exc: Exception, stage: str, model: str) -> LLMError:
    text = str(exc)
    lowered = text.lower()
    if "throttl" in lowered or "toomanyrequests" in lowered:
        return LLMRateLimited(f"{stage}: {text}")
    if "invalid_payment_instrument" in lowered:
        return LLMError(
            f"{stage}: {model!r} needs an AWS Marketplace subscription, which this "
            f"account cannot complete without a payment instrument. Amazon "
            f"first-party models (amazon.nova-*) do not have this requirement. ({text})"
        )
    if "accessdenied" in lowered or "not authorized" in lowered:
        return LLMError(
            f"{stage}: access denied for {model!r} — enable it under Bedrock "
            f"console -> Model access, in region matching AWS_REGION. ({text})"
        )
    if "could not resolve aws credentials" in lowered or "nocredentials" in lowered:
        return LLMError(
            f"{stage}: no AWS credentials found. Set AWS_ACCESS_KEY_ID and "
            f"AWS_SECRET_ACCESS_KEY in .env (or AWS_PROFILE). ({text})"
        )
    if "validationexception" in lowered:
        return LLMError(
            f"{stage}: Bedrock rejected model id {model!r}. Inference-profile ids "
            f"carry a region prefix, e.g. 'us.amazon.nova-pro-v1:0'. ({text})"
        )
    return LLMError(f"{stage}: {type(exc).__name__}: {text}")


class BedrockConverseProvider(LLMProvider):
    """Same `complete()` contract as every other adapter — no stage knows which
    family answered it, which is what makes the seam swappable at all."""

    def __init__(self, settings: Settings, model: ModelSettings) -> None:
        try:
            import boto3
        except ImportError as exc:  # pragma: no cover
            raise LLMError(
                "provider 'bedrock_converse' needs boto3: "
                "pip install 'anthropic[bedrock]'"
            ) from exc

        if not settings.aws_region:
            raise LLMError("AWS_REGION is not set (required for Bedrock)")

        kwargs: dict[str, Any] = {"region_name": settings.aws_region}
        if settings.aws_access_key_id and settings.aws_secret_access_key:
            kwargs["aws_access_key_id"] = settings.aws_access_key_id
            kwargs["aws_secret_access_key"] = settings.aws_secret_access_key
            if settings.aws_session_token:
                kwargs["aws_session_token"] = settings.aws_session_token
        elif settings.aws_profile:
            kwargs["profile_name"] = settings.aws_profile
            session = boto3.Session(profile_name=settings.aws_profile,
                                    region_name=settings.aws_region)
            self._client = session.client("bedrock-runtime")
            self._model = model
            return

        self._client = boto3.client("bedrock-runtime", **kwargs)
        self._model = model

    def complete(
        self, *, system: str, user: str, schema: dict[str, Any], stage: str
    ) -> LLMResponse:
        m = self._model
        # The schema travels in the system prompt: `converse` expresses strict
        # output through toolConfig, which is a heavier contract than this seam
        # needs, and the fence-stripping parser below covers the difference.
        instructions = (
            f"{system}\n\nReply with JSON only — no prose, no code fences — "
            f"conforming exactly to this schema:\n{json.dumps(schema)}"
        )
        try:
            response = self._client.converse(
                modelId=m.deployment,
                system=[{"text": instructions}],
                messages=[{"role": "user", "content": [{"text": user}]}],
                inferenceConfig={"maxTokens": m.max_completion_tokens or 4096},
            )
        except Exception as exc:
            raise _classify(exc, stage, m.deployment) from exc

        blocks = response.get("output", {}).get("message", {}).get("content", [])
        text = "".join(b.get("text", "") for b in blocks)
        stop_reason = response.get("stopReason", "") or ""
        if not text.strip():
            raise LLMTruncated(
                f"{stage}: empty content (stopReason={stop_reason!r}, "
                f"maxTokens={m.max_completion_tokens})"
            )

        body = strip_fences(text)
        try:
            data = json.loads(body)
        except json.JSONDecodeError as exc:
            raise LLMError(
                f"{stage}: structured output was not valid JSON: {exc}; "
                f"got {text[:160]!r}"
            ) from exc

        usage = response.get("usage", {})
        return LLMResponse(
            data=data,
            raw=text,
            input_tokens=usage.get("inputTokens", 0) or 0,
            output_tokens=usage.get("outputTokens", 0) or 0,
            model=m.deployment,
            stage=stage,
            finish_reason=stop_reason,
        )
