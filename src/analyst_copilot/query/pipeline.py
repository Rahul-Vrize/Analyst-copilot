"""THE SPINE - the only place stage order lives (§18.1, §24.2).

    route -> navigate -> RETRIEVE -> extract -> COMPUTE -> verify -> ANSWER

One spine with switchable stages, NOT three pipelines. A misroute would send a
question down a path that structurally cannot answer it, and verification would
be duplicated and drift apart.

⚠️ THIS IS A DETERMINISTIC WORKFLOW, NOT AN AGENT (D13). Control flow lives
here, in code. The model chooses CONTENT - which evidence, which formula - never
what happens next. The honest label is "structure-aware RAG with an
evidence-first verification gate".

Escalation is by DEPTH, not breadth (Decision E, §20.1): ✅ MEASURED that the
gold filing is in router top-4 for 134/136 and in top-8 for the same 134/136, so
widening the document set buys nothing. One retry, deeper inside the same
candidates.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from ..config import Settings
from ..llm import schemas
from ..llm.base import LLMError, LLMProvider
from ..llm.registry import load_prompt
from ..retrieval.anchors import AnchorRetriever
from ..retrieval.assemble import assemble
from ..retrieval.base import Hit
from ..retrieval.bm25 import BM25Index
from ..retrieval.rerank import maybe_reranker
from .compose import compose_answer
from .compute import Computation, FormulaError, Operand, compute
from .extract import Extraction, extract_evidence
from .formula_book import FormulaBook, FormulaChoice, FormulaSource
from .gates import AnswerCandidate, Citation, GateReport, run_gates
from .router import DocumentRouter, RouteResult


@dataclass
class QueryResult:
    """Mirrors `api/schemas.py::AnswerResponse` (§22.5)."""

    status: str                          # answered | abstained | clarify
    answer: str | None = None
    clarifying_question: str | None = None
    citations: list[Citation] = field(default_factory=list)
    computation: Computation | None = None
    definition: str | None = None
    formula_source: str | None = None
    abstain_reason: str | None = None
    trace: dict[str, Any] = field(default_factory=dict)


class QueryPipeline:
    def __init__(
        self,
        settings: Settings,
        router: DocumentRouter,
        bm25: BM25Index,
        anchors: AnchorRetriever,
        pages_by_doc: dict[str, dict[int, str]],
        headers_by_page: dict[str, str],
        coverage_years: dict[str, list[int]],
        extractor: LLMProvider,
        verifier_a: LLMProvider | None = None,
        verifier_b: LLMProvider | None = None,
        formula_book: FormulaBook | None = None,
        router_llm: LLMProvider | None = None,
        composer: LLMProvider | None = None,
    ) -> None:
        self.settings = settings
        self.router = router
        self.bm25 = bm25
        self.anchors = anchors
        self.pages_by_doc = pages_by_doc
        self.headers_by_page = headers_by_page
        self.coverage_years = coverage_years
        self.extractor = extractor
        self.verifier_a = verifier_a
        self.verifier_b = verifier_b
        self.book = formula_book or FormulaBook()
        self.router_llm = router_llm
        self.composer = composer

    # ------------------------------------------------------------------
    def answer(self, question: str) -> QueryResult:
        started = time.time()
        trace: dict[str, Any] = {"query_id": str(uuid.uuid4())}

        # ── STAGE 1: route ────────────────────────────────────────────
        route = self.router.route(question)
        trace["candidate_docs"] = [c.doc_id for c in route.candidates]
        trace["company_named"] = route.company_named

        # D1: a wrong document costs -1; a clarifying question is free.
        if route.needs_clarification or not route.candidates:
            return QueryResult(
                status="clarify",
                clarifying_question=(
                    "Which company's filing should I look at? I hold filings for "
                    "32 companies and the question does not name one."
                ),
                trace={**trace, "latency_ms": int((time.time() - started) * 1000)},
            )

        intent, question_years = self._route_metadata(question, route)
        trace["intent"] = intent

        # ── STAGES 2-6, tier 1 then one deeper retry (Decision E) ─────
        result = self._attempt(question, route, intent, question_years, trace, tier=1)
        if result.status == "answered":
            result.trace = {**trace, "tier": 1,
                            "latency_ms": int((time.time() - started) * 1000)}
            return result

        retry = self._attempt(question, route, intent, question_years, trace, tier=2)
        retry.trace = {**trace, "tier": 2,
                       "latency_ms": int((time.time() - started) * 1000),
                       "tier1_abstain_reason": result.abstain_reason}
        return retry

    # ------------------------------------------------------------------
    def _route_metadata(
        self, question: str, route: RouteResult
    ) -> tuple[str, list[int]]:
        """Intent detection. Deterministic years; the LLM only adds intent.

        `filing_period` != `question_period`: "What is Boeing forecasting for
        FY2023?" has no FY2023 filing, so forecast intent must search EARLIER
        filings for forward-looking statements. Years are never substituted.
        """
        years = sorted(route.years)
        if self.router_llm is None:
            return "historical", years
        try:
            name, schema = schemas.BY_STAGE["router"]
            data = self.router_llm.complete(
                system=load_prompt(name), user=question, schema=schema, stage="router"
            ).data
            return (data.get("intent") or "historical"), years
        except LLMError:
            return "historical", years

    def _retrieve(
        self, question: str, scope: list[str], tier: int
    ) -> tuple[list[list[Hit]], int]:
        k = (
            self.settings.retrieval.bm25_top_k
            if tier == 1
            else self.settings.retrieval.bm25_top_k_escalated
        )
        rankings = [
            self.anchors.search(question, scope, k=40),
            self.bm25.search(question, scope, k, per_document=True),
        ]
        return rankings, k

    def _attempt(
        self,
        question: str,
        route: RouteResult,
        intent: str,
        question_years: list[int],
        trace: dict[str, Any],
        *,
        tier: int,
    ) -> QueryResult:
        scope = [c.doc_id for c in route.candidates]
        rankings, k = self._retrieve(question, scope, tier)

        expand = self.settings.retrieval.neighbour_expand * tier
        context = assemble(
            rankings,
            self.pages_by_doc,
            token_budget=self.settings.retrieval.assembly_token_budget,
            rrf_k=self.settings.retrieval.rrf_k,
            neighbour_expand=expand,
            reranker=maybe_reranker(self.settings, question),
            rerank_top_n=self.settings.retrieval.rerank_top_n,
            headers_by_page=self.headers_by_page,
        )
        trace[f"tier{tier}_pages"] = context.page_count
        trace[f"tier{tier}_tokens"] = context.token_estimate
        trace[f"tier{tier}_bm25_k"] = k

        if not context.hits:
            return self._abstain("no_candidates")

        # ── STAGE 4: extract ──────────────────────────────────────────
        try:
            extraction = extract_evidence(self.extractor, question, context.text)
        except LLMError as exc:
            # A truncated or failed model call is NOT evidence of absence, but
            # we still cannot answer, so we abstain and record why. The MESSAGE
            # is kept, not just the type: an abstention caused by a deployment
            # quota limit must be distinguishable from one caused by the filing
            # genuinely lacking the evidence, or the abstention rate is
            # meaningless.
            trace.setdefault("errors", []).append(f"tier{tier}:{exc}")
            return self._abstain(f"extractor_error:{type(exc).__name__}")

        # ── STAGE 5: compute ──────────────────────────────────────────
        choice, computation, compute_error = self._compute(question, extraction)

        composed = compose_answer(
            extraction, computation, question, question_years, self.composer
        )
        if not composed.answerable or not composed.text:
            # The evidence did not settle the question. Declining here is the
            # correct outcome, not a failure (§25.4).
            return self._abstain("unanswerable_from_evidence")
        answer_text = composed.text

        # ── STAGE 6: verify - deterministic gates first ───────────────
        candidate = AnswerCandidate(
            question=question,
            answer_text=answer_text,
            citations=extraction.citations,
            missing_slots=extraction.missing_slots + ([compute_error] if compute_error else []),
            operands=extraction.operands,
            computation=computation,
            page_texts=context.pages_by_doc,
            candidate_doc_ids=scope,
            coverage_years={d: self.coverage_years.get(d, []) for d in scope},
            evidence_years=self._evidence_years(extraction),
            intent=intent,
            question_years=question_years,
            answer_type=extraction.answer_type,
        )
        report = run_gates(candidate, enabled=self.settings.verification.enabled_gates)
        trace[f"tier{tier}_gates"] = [
            {"gate": r.gate_id, "passed": r.passed, "detail": r.detail}
            for r in report.results
        ]
        if not report.passed:
            return self._abstain(report.abstain_reason or "gates")

        # ── STAGE 6b: the two LLM verifiers, last net ─────────────────
        verdicts = self._verify(question, answer_text, extraction)
        trace[f"tier{tier}_verifiers"] = verdicts
        if verdicts and not all(verdicts.values()):
            failed = [k for k, v in verdicts.items() if not v]
            return self._abstain(f"verifier:{','.join(failed)}")

        return QueryResult(
            status="answered",
            answer=answer_text,
            citations=extraction.citations,
            computation=computation,
            definition=choice.definition if choice else None,
            formula_source=choice.source.value if choice else None,
        )

    # ------------------------------------------------------------------
    def _compute(
        self, question: str, extraction: Extraction
    ) -> tuple[FormulaChoice | None, Computation | None, str | None]:
        """Apply the precedence ladder, then evaluate in Decimal (D5)."""
        hint = extraction.metric_name or extraction.question_supplied_definition
        choice = self.book.choose(question, hint)
        if choice is None or not choice.formula:
            return choice, None, None
        try:
            computation = compute(
                choice.formula,
                extraction.operands,
                unit=choice.unit,
                render=choice.render,
                dp=choice.dp,
            )
        except FormulaError as exc:
            # A formula we cannot evaluate is a MISSING OPERAND, not a licence
            # to answer from the narrative.
            return choice, None, f"formula:{exc}"
        return choice, computation, None

    @staticmethod
    def _evidence_years(extraction: Extraction) -> list[int]:
        years: list[int] = []
        for slot in extraction.slots:
            for token in (slot.period or "").split():
                digits = "".join(ch for ch in token if ch.isdigit())
                if len(digits) == 4 and digits.startswith("20"):
                    years.append(int(digits))
        return sorted(set(years))

    def _verify(
        self, question: str, answer_text: str, extraction: Extraction
    ) -> dict[str, bool]:
        """Verifiers A and B, each in ISOLATION.

        ⚠️ Verifier B receives ONLY question + answer + quotes. Never verifier
        A's verdict, never the extractor's reasoning, never the retrieval trace.
        Context isolation is the part we keep regardless of model family, and
        with both verifiers on gpt-5-mini it is most of what independence we
        have (§8e).
        """
        if not (self.verifier_a or self.verifier_b):
            return {}
        quotes = "\n".join(
            f"[{s.doc_id} p.{s.page_seq}] {s.quote}" for s in extraction.slots
        )
        user = f"QUESTION:\n{question}\n\nPROPOSED ANSWER:\n{answer_text}\n\nQUOTES:\n{quotes}"

        verdicts: dict[str, bool] = {}
        if self.verifier_a is not None:
            verdicts["a"] = self._one_verdict(
                self.verifier_a, "verifier_a", load_prompt("verify"),
                user, schemas.VERIFY, ok="VALID",
            )
        if self.verifier_b is not None:
            adversarial = self.settings.verification.verifier_b_adversarial
            verdicts["b"] = self._one_verdict(
                self.verifier_b,
                "verifier_b",
                load_prompt("verify_adversarial" if adversarial else "verify"),
                user,
                schemas.VERIFY_ADVERSARIAL if adversarial else schemas.VERIFY,
                ok="SUPPORTED" if adversarial else "VALID",
            )
        return verdicts

    @staticmethod
    def _one_verdict(provider, stage, system, user, schema, *, ok: str) -> bool:
        try:
            data = provider.complete(
                system=system, user=user, schema=schema, stage=stage
            ).data
        except LLMError:
            # A verifier that cannot run has not approved anything. Fail closed.
            return False
        return (data.get("verdict") or "").upper() == ok

    def _abstain(self, reason: str) -> QueryResult:
        return QueryResult(
            status="abstained",
            answer=self.settings.verification.abstain_string,
            abstain_reason=reason,
        )
