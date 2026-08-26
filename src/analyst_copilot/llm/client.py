"""Single entry point for LLM calls. Keeping every call behind this thin
wrapper is what makes the "bounded planner with observable state" (design
doc, Synthesis) auditable — count calls, cap iteration, and swap models
without hunting through the codebase.

Reserve LLM calls for query planning, semantic synthesis, and verification
of difficult cases (design doc: "inexpensive deterministic mechanisms
handle high-frequency lookup and arithmetic, while LLM reasoning is
reserved for" those). Never use it to do arithmetic or to invent facts not
present in the supplied evidence.
"""

from __future__ import annotations

from anthropic import Anthropic

from analyst_copilot.config import settings


class LLMClient:
    def __init__(self, model: str | None = None, api_key: str | None = None) -> None:
        self.model = model or settings.llm_model
        self._client = Anthropic(api_key=api_key or settings.anthropic_api_key)
        self.call_count = 0  # observable state: how many LLM calls this session made

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = 1024,
    ) -> str:
        self.call_count += 1
        response = self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system or "",
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(
            block.text for block in response.content if getattr(block, "type", None) == "text"
        )
