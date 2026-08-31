"""Stage -> provider resolution, and prompt loading (§22.7).

Adding a model family is ONE adapter file plus ONE entry in `_ADAPTERS`.
Nothing else in the system changes, which is what makes moving verifier B to a
genuine second family a `.env` edit:

    VERIFIER_B_PROVIDER=anthropic_foundry
    VERIFIER_B_DEPLOYMENT=claude-haiku-4-5

⚠️ Prompts are VERSIONED FILES, never inlined in code (§24.2). A prompt is a
tuned artifact - the query-rewrite prompt alone moved BM25 R@10 from 16.7% to
37.3% - so it belongs under review like any other measured component.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Callable

from ..config import Settings
from .base import Embedder, LLMProvider, Reranker

PROMPT_DIR = Path(__file__).parent / "prompts"


class UnknownProvider(RuntimeError):
    pass


@lru_cache(maxsize=64)
def load_prompt(name: str) -> str:
    """Load `llm/prompts/<name>.txt`. Cached; prompts do not change at runtime."""
    path = PROMPT_DIR / f"{name}.txt"
    if not path.exists():
        raise FileNotFoundError(f"prompt {name!r} not found at {path}")
    return path.read_text(encoding="utf-8").strip()


def _azure_openai(settings: Settings, model) -> LLMProvider:
    from .azure_openai import AzureOpenAIProvider

    return AzureOpenAIProvider(settings, model)


def _anthropic_foundry(settings: Settings, model) -> LLMProvider:
    from .anthropic_foundry import AnthropicFoundryProvider

    return AnthropicFoundryProvider(settings, model)


def _bedrock(settings: Settings, model) -> LLMProvider:
    """Claude on AWS Bedrock — the only route to a genuine second family here.

    Claude on Microsoft Foundry bills through Azure Marketplace, which a
    credit-only subscription cannot purchase; Bedrock bills as ordinary AWS
    usage against the account's credits.
    """
    from .bedrock import BedrockProvider

    return BedrockProvider(settings, model)


def _bedrock_converse(settings: Settings, model) -> LLMProvider:
    """Amazon Nova on Bedrock — the second family that is actually reachable.

    Claude is Marketplace-subscribed on BOTH Azure Foundry and AWS Bedrock, and
    a credit-only account cannot complete either subscription. Amazon's own
    models are first-party and have no such requirement.
    """
    from .bedrock_converse import BedrockConverseProvider

    return BedrockConverseProvider(settings, model)


_ADAPTERS: dict[str, Callable[[Settings, object], LLMProvider]] = {
    "azure_openai": _azure_openai,
    "anthropic_foundry": _anthropic_foundry,
    "bedrock": _bedrock,
    "bedrock_converse": _bedrock_converse,
}


def get_provider(settings: Settings, stage: str) -> LLMProvider:
    """Resolve config.yaml `models.<stage>` to a live adapter.

    stage in {router, planner, query_rewrite, section_titles, summarizer,
              extractor, formula_pick, verifier_a, verifier_b, judge}
    """
    model = settings.model(stage)
    factory = _ADAPTERS.get(model.provider)
    if factory is None:
        raise UnknownProvider(
            f"stage {stage!r} wants provider {model.provider!r}; "
            f"known providers: {sorted(_ADAPTERS)}"
        )
    return factory(settings, model)


def get_embedder(settings: Settings) -> Embedder:
    from .azure_openai import AzureOpenAIEmbedder

    model = settings.model("embeddings")
    if model.provider != "azure_openai":
        raise UnknownProvider(f"no embedder adapter for {model.provider!r}")
    return AzureOpenAIEmbedder(settings, model)


def get_reranker(settings: Settings) -> Reranker:
    from .cohere_foundry import CohereFoundryReranker

    return CohereFoundryReranker(settings, settings.model("reranker"))


def verifier_prompt(settings: Settings) -> str:
    """Which verifier prompt verifier B gets, per `verification.independence`.

    ⚠️ REFUTATION FRAMING IS A SUBSTITUTE FOR INDEPENDENCE, NOT AN ADDITION TO
    IT. With a genuine second family, set `verifier_b_adversarial: false` -
    running both over-abstains and costs answerable questions, and the
    abstention threshold must be re-calibrated after any such switch.
    """
    if settings.verification.verifier_b_adversarial:
        return load_prompt("verify_adversarial")
    return load_prompt("verify")
