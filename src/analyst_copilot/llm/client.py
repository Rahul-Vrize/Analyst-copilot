"""Single entry point for LLM calls. Keeping every call behind this thin
wrapper is what makes the "bounded planner with observable state" (design
doc, Synthesis) auditable — count calls, cap iteration, and swap models or
providers without hunting through the codebase.

Reserve LLM calls for query planning, semantic synthesis, and verification
of difficult cases (design doc: "inexpensive deterministic mechanisms
handle high-frequency lookup and arithmetic, while LLM reasoning is
reserved for" those). Never use it to do arithmetic or to invent facts not
present in the supplied evidence.

Supports two providers, selected by LLM_PROVIDER in .env:
  - "anthropic": direct Anthropic API (ANTHROPIC_API_KEY)
  - "bedrock": AWS Bedrock Converse API (AWS_REGION + credentials +
    BEDROCK_MODEL) — provider-agnostic across Bedrock model families, so it
    works whether BEDROCK_MODEL is a Claude model or (as currently
    configured) an OpenAI OSS model hosted on Bedrock.
"""

from __future__ import annotations

from analyst_copilot.config import settings


class LLMClient:
    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        provider: str | None = None,
    ) -> None:
        self.provider = provider or settings.llm_provider
        self.call_count = 0  # observable state: how many LLM calls this session made

        if self.provider == "bedrock":
            import boto3

            self.model = model or settings.bedrock_model
            if not self.model:
                raise ValueError("BEDROCK_MODEL is not set")
            self._bedrock = boto3.client("bedrock-runtime", region_name=settings.aws_region)
        else:
            from anthropic import Anthropic

            self.model = model or settings.llm_model
            self._client = Anthropic(api_key=api_key or settings.anthropic_api_key)

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = 1024,
    ) -> str:
        self.call_count += 1
        if self.provider == "bedrock":
            return self._complete_bedrock(prompt, system=system, max_tokens=max_tokens)
        return self._complete_anthropic(prompt, system=system, max_tokens=max_tokens)

    def _complete_anthropic(self, prompt: str, *, system: str | None, max_tokens: int) -> str:
        response = self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system or "",
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(
            block.text for block in response.content if getattr(block, "type", None) == "text"
        )

    def _complete_bedrock(self, prompt: str, *, system: str | None, max_tokens: int) -> str:
        kwargs = {}
        if system:
            kwargs["system"] = [{"text": system}]
        response = self._bedrock.converse(
            modelId=self.model,
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": max_tokens},
            **kwargs,
        )
        content = response["output"]["message"]["content"]
        return "".join(block.get("text", "") for block in content)
