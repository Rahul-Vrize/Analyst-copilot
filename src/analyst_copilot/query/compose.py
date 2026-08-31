"""The ANSWER stage - composing the final answer from evidence (§18.1).

⚠️ THE MAJORITY CASE IS NOT NUMBER EXTRACTION. ✅ MEASURED: 84 of 136 gold
answers (62%) are non-numeric - judgements, phrases, explanations. A pipeline
that renders only a figure answers 38% of questions in the right shape and
returns a bare number for the rest.

MEASURED FAILURE that produced this module: asked "which segment has dragged
down 3M's overall growth in 2022?" (gold: "The consumer segment shrunk by 0.9%
organically") the pipeline returned `8,902` - the first numeric slot. Every
deterministic gate passed, because the figure was correctly quoted and correctly
located. It was simply not an answer to the question. That is a -1 the gates
cannot catch, because it is a RELEVANCE failure, not a grounding failure.

So the answer shape switches here:
  * a computed metric  -> the calculator's result, rendered (no model involved)
  * a bare scalar      -> the matching slot's value
  * anything else      -> composed from the quotes by the model, under the same
                          evidence-only constraint as extraction

The composer may only use the supplied quotes, and it may decline. G1 still
checks every citation afterwards, so composition cannot introduce a figure that
is not on the cited page.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..llm import schemas
from ..llm.base import LLMError, LLMProvider
from ..llm.registry import load_prompt
from .compute import Computation
from .extract import Extraction

@dataclass
class ComposedAnswer:
    text: str
    answerable: bool = True
    used_model: bool = False


def _pick_slot(extraction: Extraction, question_years: list[int]):
    """The slot whose period matches the question.

    A statement presents three years side by side, so the extractor routinely
    returns the same line item for several periods. Taking the first is a
    plausible figure from the wrong year - the -1 no gate can catch.
    """
    valued = [s for s in extraction.slots if s.value is not None] or extraction.slots
    if not valued:
        return None
    if question_years:
        target = str(max(question_years))
        matching = [s for s in valued if target in (s.period or "")]
        if matching:
            return matching[0]
    return valued[0]


def compose_answer(
    extraction: Extraction,
    computation: Computation | None,
    question: str,
    question_years: list[int],
    composer: LLMProvider | None = None,
) -> ComposedAnswer:
    """Render the final answer in the shape the question asked for."""
    # A computed metric is rendered by the calculator, never by the model (D5).
    if computation is not None:
        return ComposedAnswer(computation.rendered())

    if not extraction.slots:
        return ComposedAnswer("", answerable=False)

    # ⚠️ EVEN A SCALAR GOES THROUGH THE COMPOSER when one is available.
    # MEASURED: returning the bare slot value gave the answer "1,577" for
    # "What is the FY2018 capital expenditure amount (in USD millions) for 3M?".
    # Every gate passed, and verifier A then rejected it - correctly - because a
    # naked figure states neither its unit nor its period, so it cannot be
    # checked for the scale and period consistency the verifier is asked to
    # confirm. "$1,577 million in FY2018" is the same fact, verifiable.
    if composer is None:
        slot = _pick_slot(extraction, question_years)
        return ComposedAnswer(
            str(slot.value) if slot and slot.value is not None else (slot.quote if slot else ""),
        )

    quotes = "\n".join(
        f"[{i}] ({s.doc_id} p.{s.page_seq}) {s.name}: {s.value} — \"{s.quote}\""
        for i, s in enumerate(extraction.slots)
    )
    prompt_name, schema = schemas.BY_STAGE["composer"]
    try:
        data = composer.complete(
            system=load_prompt(prompt_name),
            user=f"QUESTION:\n{question}\n\nEVIDENCE QUOTES:\n{quotes}",
            schema=schema,
            stage="composer",
        ).data
    except LLMError:
        # Composition failing is not evidence of absence, but we cannot answer.
        return ComposedAnswer("", answerable=False)

    if not data.get("answerable", True):
        return ComposedAnswer("", answerable=False)
    return ComposedAnswer(
        (data.get("answer") or "").strip(), answerable=True, used_model=True
    )
