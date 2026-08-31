"""The LLM seam: schema/prompt consistency and family-swap safety (§22.7).

These run OFFLINE. They do not call a model - they check the contract that makes
a model call safe, which is the part that broke in practice.
"""

from __future__ import annotations

import re

import pytest

from analyst_copilot.config import load_settings
from analyst_copilot.llm import schemas
from analyst_copilot.llm.registry import PROMPT_DIR, load_prompt, verifier_prompt

# A field name as written in a prompt's "  name : description" listing.
_PROMPT_FIELD = re.compile(r"^\s{2}([a-z_]+)\s*:", re.M)


def _all_property_names(node: dict) -> set[str]:
    """Every property name at any depth.

    A prompt lists the fields of one ITEM ("clean_title", "kind"), while the
    schema may wrap them in an array ("sections"), so the comparison has to
    recurse or it reports false positives.
    """
    names: set[str] = set()
    if node.get("type") == "object":
        for name, child in node.get("properties", {}).items():
            names.add(name)
            names |= _all_property_names(child)
    elif node.get("type") == "array":
        names |= _all_property_names(node["items"])
    return names


def test_every_prompt_file_loads():
    for path in PROMPT_DIR.glob("*.txt"):
        assert load_prompt(path.stem).strip(), f"{path.name} is empty"


@pytest.mark.parametrize("stage", sorted(schemas.BY_STAGE))
def test_prompt_and_schema_declare_the_same_fields(stage):
    """MEASURED FAILURE: the router prompt listed `form_types` and `periods`
    while the schema omitted them, and gpt-5-mini crammed the missing fields
    into a string field rather than choose between two contradicting
    instructions (§23.3)."""
    prompt_name, schema = schemas.BY_STAGE[stage]
    prompt = load_prompt(prompt_name)
    declared = set(_PROMPT_FIELD.findall(prompt))
    if not declared:
        pytest.skip(f"{prompt_name} does not enumerate fields")
    missing = declared - _all_property_names(schema)
    assert not missing, (
        f"{prompt_name}.txt asks for {sorted(missing)} which "
        f"{stage}'s schema does not declare"
    )


@pytest.mark.parametrize("stage", sorted(schemas.BY_STAGE))
def test_schemas_are_azure_strict_mode_compatible(stage):
    """Strict mode requires additionalProperties:false and every property in
    `required`; an optional field must be a nullable type, not an absent one."""

    def check(node: dict) -> None:
        if node.get("type") == "object":
            assert node.get("additionalProperties") is False
            assert set(node.get("required", [])) == set(node["properties"])
            for child in node["properties"].values():
                check(child)
        elif node.get("type") == "array":
            check(node["items"])

    check(schemas.BY_STAGE[stage][1])


def test_the_extractor_schema_requires_a_quote_and_a_location():
    """Gate G1 checks `quote` against the cited page, so both must be present
    on every slot or the gate has nothing to check."""
    slot = schemas.EXTRACT["properties"]["slots"]["items"]
    for field in ("quote", "doc_id", "page_seq"):
        assert field in slot["properties"]
        assert field in slot["required"]


def test_the_extractor_can_report_missing_slots():
    """§23.2: our prompts invert OpenAI's escape hatch - the model needs a
    cheap, explicit way to STOP rather than proceed under uncertainty."""
    assert "missing_slots" in schemas.EXTRACT["properties"]
    prompt = load_prompt("extract")
    assert "missing_slots" in prompt
    assert "stop" in prompt.lower()


def test_extractor_prompt_forbids_using_model_knowledge():
    prompt = load_prompt("extract").lower()
    assert "verbatim" in prompt
    assert "own knowledge" in prompt


def test_verifier_prompt_follows_the_independence_setting():
    """Refutation framing is a SUBSTITUTE for a second family, not an addition."""
    settings = load_settings()
    prompt = verifier_prompt(settings)
    if settings.verification.verifier_b_adversarial:
        assert "REFUTE" in prompt
        assert "Default to REFUTED when uncertain" in prompt
    else:
        assert "VALID" in prompt


def test_verifier_b_is_configured_and_swappable():
    """The swap to a real second family must stay a .env change (§22.7)."""
    settings = load_settings()
    assert settings.model("verifier_b").provider
    assert settings.model("verifier_b").deployment
    assert settings.verification.independence in {
        "same_model_adversarial",
        "different_model",
        "different_family",
    }


def test_anthropic_adapter_exists_so_the_swap_path_is_real():
    """An untested swap path that is 'designed' but not written is not a swap
    path. Import only - we have no Claude quota to call it."""
    from analyst_copilot.llm import anthropic_foundry

    assert hasattr(anthropic_foundry, "AnthropicFoundryProvider")


def test_reasoning_truncation_has_its_own_error_type():
    """gpt-5-mini returns content='' with finish_reason='length' and NO error
    when the budget is spent on hidden reasoning. That must never be mistaken
    for a refusal, which would silently become an abstention."""
    from analyst_copilot.llm.base import LLMError, LLMTruncated

    assert issubclass(LLMTruncated, LLMError)
