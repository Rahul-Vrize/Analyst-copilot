"""Anthropic-on-Foundry adapter - THE SWAP PATH, built rather than deferred.

We currently have no Claude quota, so this adapter is unused at runtime. It
exists because §22.7 requires that restoring genuine two-family verification
(D4) is a `.env` change with no code change, and an untested swap path that is
"designed" but not written is not a swap path.

⚠️ FOUR TRAPS THIS ENCODES, all recorded in HANDOFF §9:
  * the client is `AnthropicFoundry(api_key=, resource=)` - NOT `Anthropic()`
    with a `base_url` override
  * the system prompt is a TOP-LEVEL `system=` parameter, not a message
  * structured output is `output_config.format`, not `response_format`
  * on Foundry, Opus 4.8 runs WITHOUT thinking if `thinking` is omitted (unlike
    Opus 5, where adaptive is the default). `budget_tokens` returns a 400.
"""

from __future__ import annotations

import json
from typing import Any

from ..config import ModelSettings, Settings
from .base import LLMError, LLMProvider, LLMResponse, LLMTruncated


class AnthropicFoundryProvider(LLMProvider):
    def __init__(self, settings: Settings, model: ModelSettings) -> None:
        try:
            from anthropic import AnthropicFoundry  # type: ignore[attr-defined]
        except ImportError as exc:  # pragma: no cover - only on the swap path
            raise LLMError(
                "the anthropic SDK is required for provider 'anthropic_foundry'"
            ) from exc

        if not settings.azure_foundry_api_key:
            raise LLMError("AZURE_FOUNDRY_API_KEY is not set")
        if not settings.azure_foundry_resource:
            raise LLMError("AZURE_FOUNDRY_RESOURCE is not set")

        self._client = AnthropicFoundry(
            api_key=settings.azure_foundry_api_key,
            resource=settings.azure_foundry_resource,
        )
        self._model = model

    def complete(
        self, *, system: str, user: str, schema: dict[str, Any], stage: str
    ) -> LLMResponse:
        m = self._model
        kwargs: dict[str, Any] = {
            "model": m.deployment,
            # Top-level system parameter, NOT a {"role": "system"} message.
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "max_tokens": m.max_completion_tokens or 4096,
            "output_config": {"format": {"type": "json_schema", "schema": schema}},
        }
        if m.thinking:
            # Must be passed explicitly; omitting it disables thinking on 4.8.
            kwargs["thinking"] = {"type": m.thinking}

        try:
            resp = self._client.messages.create(**kwargs)
        except Exception as exc:
            raise LLMError(f"{stage}: {type(exc).__name__}: {exc}") from exc

        text = "".join(
            block.text for block in resp.content if getattr(block, "type", "") == "text"
        )
        stop_reason = getattr(resp, "stop_reason", "") or ""
        if not text:
            raise LLMTruncated(
                f"{stage}: empty content (stop_reason={stop_reason!r}, "
                f"max_tokens={kwargs['max_tokens']})"
            )

        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise LLMError(f"{stage}: structured output was not valid JSON: {exc}") from exc

        usage = getattr(resp, "usage", None)
        return LLMResponse(
            data=data,
            raw=text,
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            model=m.deployment,
            stage=stage,
            finish_reason=stop_reason,
        )
