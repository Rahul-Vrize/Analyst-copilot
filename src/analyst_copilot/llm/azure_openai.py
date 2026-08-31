"""Azure OpenAI adapter - one of only two modules allowed to import a vendor SDK.

⚠️ AZURE EXPOSES TWO INCOMPATIBLE CLIENT SHAPES AND MIXING THEM GIVES 404s:

    endpoint ending /openai/v1  ->  OpenAI(base_url=..., api_key=...)
    classic endpoint            ->  AzureOpenAI(azure_endpoint=, api_version=)

Our deployment is the `/openai/v1` shape, so passing it to the classic client
fails with 404s that look like a missing deployment. The client shape is
detected from the URL rather than configured, because getting it wrong produces
a misleading error.
"""

from __future__ import annotations

import json
from typing import Any

from ..config import ModelSettings, Settings
from .base import (
    Embedder,
    LLMError,
    LLMProvider,
    LLMRateLimited,
    LLMResponse,
    LLMTruncated,
    with_retry,
)


def _is_rate_limit(exc: Exception) -> bool:
    """Throttling, in whatever shape the SDK surfaces it.

    Checked structurally where possible and textually as a fallback, because a
    429 misclassified as a hard error becomes a false abstention.
    """
    status = getattr(exc, "status_code", None) or getattr(exc, "http_status", None)
    if status == 429:
        return True
    name = type(exc).__name__.lower()
    if "ratelimit" in name:
        return True
    text = str(exc).lower()
    return "429" in text or "rate limit" in text or "too many requests" in text


def _build_client(settings: Settings):
    from openai import AzureOpenAI, OpenAI  # imported here: see module docstring

    endpoint = (settings.azure_openai_endpoint or "").strip().rstrip("/")
    if not endpoint:
        raise LLMError("AZURE_OPENAI_ENDPOINT is not set")
    api_key = settings.azure_openai_api_key
    if not api_key:
        raise LLMError("AZURE_OPENAI_API_KEY is not set")

    if "/openai/v1" in endpoint:
        return OpenAI(base_url=endpoint, api_key=api_key)
    return AzureOpenAI(
        azure_endpoint=endpoint,
        api_key=api_key,
        api_version=settings.azure_openai_api_version or "2024-10-21",
    )


class AzureOpenAIProvider(LLMProvider):
    def __init__(self, settings: Settings, model: ModelSettings) -> None:
        self._client = _build_client(settings)
        self._model = model

    def complete(
        self, *, system: str, user: str, schema: dict[str, Any], stage: str
    ) -> LLMResponse:
        return with_retry(lambda: self._complete_once(system, user, schema, stage))

    def _complete_once(
        self, system: str, user: str, schema: dict[str, Any], stage: str
    ) -> LLMResponse:
        m = self._model
        kwargs: dict[str, Any] = {
            "model": m.deployment,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            # Every call is structured; no stage parses free text (§22.7 rule 2).
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": f"{stage}_output",
                    "strict": True,
                    "schema": schema,
                },
            },
        }
        if m.max_completion_tokens:
            kwargs["max_completion_tokens"] = m.max_completion_tokens
        if m.reasoning_effort:
            kwargs["reasoning_effort"] = m.reasoning_effort
        if m.verbosity:
            kwargs["verbosity"] = m.verbosity

        try:
            resp = self._client.chat.completions.create(**kwargs)
        except Exception as exc:
            if _is_rate_limit(exc):
                raise LLMRateLimited(f"{stage}: {exc}") from exc
            raise LLMError(f"{stage}: {type(exc).__name__}: {exc}") from exc

        choice = resp.choices[0]
        content = choice.message.content or ""
        finish = choice.finish_reason or ""

        usage = getattr(resp, "usage", None)
        reasoning = 0
        if usage is not None:
            details = getattr(usage, "completion_tokens_details", None)
            reasoning = getattr(details, "reasoning_tokens", 0) or 0

        if not content:
            # The reasoning-model trap: empty content with finish_reason
            # 'length' is a BUDGET error, not a model failure. Say so.
            raise LLMTruncated(
                f"{stage}: empty content (finish_reason={finish!r}, "
                f"reasoning_tokens={reasoning}, "
                f"max_completion_tokens={m.max_completion_tokens}). "
                "Raise max_completion_tokens for this stage in config.yaml."
            )

        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            raise LLMError(f"{stage}: structured output was not valid JSON: {exc}") from exc

        return LLMResponse(
            data=data,
            raw=content,
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0,
            reasoning_tokens=reasoning,
            model=m.deployment,
            stage=stage,
            finish_reason=finish,
        )


class AzureOpenAIEmbedder(Embedder):
    def __init__(self, settings: Settings, model: ModelSettings) -> None:
        self._client = _build_client(settings)
        self._model = model

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        kwargs: dict[str, Any] = {"model": self._model.deployment, "input": texts}
        if self._model.dimensions:
            # MUST equal pages.embedding vector(N) or every insert fails, and it
            # fails at the END of a long ingest. config.py asserts this too.
            kwargs["dimensions"] = self._model.dimensions
        try:
            resp = self._client.embeddings.create(**kwargs)
        except Exception as exc:
            if _is_rate_limit(exc):
                raise LLMRateLimited(f"embeddings: {exc}") from exc
            raise LLMError(f"embeddings: {type(exc).__name__}: {exc}") from exc
        return [d.embedding for d in resp.data]
