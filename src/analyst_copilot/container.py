"""Composition root - builds every stage from Settings and wires them (§24.2).

This is the ONLY module that knows how the pieces fit together. Stages receive
their dependencies here; none of them constructs a provider, opens a connection,
or reads config for itself (§24.1).

Building the pipeline loads the whole corpus index into memory once: the BM25
index over ~8,400 pages builds in well under a second, so a service holds one
container for its lifetime and rebuilds it after an ingest.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import Settings, load_settings
from .ingest.catalog import build_catalog
from .llm.registry import get_provider
from .query.formula_book import FormulaBook
from .query.pipeline import QueryPipeline
from .query.router import DocumentRouter, load_aliases
from .retrieval.anchors import build_from_rows
from .retrieval.bm25 import BM25Index
from .storage import repository as repo
from .storage.db import connect


@dataclass
class Corpus:
    """Everything retrieval needs, loaded once."""

    bm25: BM25Index
    anchors: object
    pages_by_doc: dict[str, dict[int, str]]
    headers_by_page: dict[str, str]
    coverage_years: dict[str, list[int]]
    n_pages: int
    n_docs: int


def load_corpus(settings: Settings) -> Corpus:
    with connect(settings.database_url) as conn:
        rows = repo.pages_for_bm25(conn)
        catalog_rows = repo.load_catalog(conn)

    pages_by_doc: dict[str, dict[int, str]] = {}
    headers_by_page: dict[str, str] = {}
    for r in rows:
        pages_by_doc.setdefault(r["doc_id"], {})[r["page_seq"]] = r["raw_text"] or ""
        headers_by_page[r["page_id"]] = r.get("context_header") or ""

    coverage = {
        r["doc_id"]: list(r.get("coverage_years") or []) for r in catalog_rows
    }
    return Corpus(
        bm25=BM25Index(rows),
        anchors=build_from_rows(rows),
        pages_by_doc=pages_by_doc,
        headers_by_page=headers_by_page,
        coverage_years=coverage,
        n_pages=len(rows),
        n_docs=len(pages_by_doc),
    )


def build_pipeline(
    settings: Settings | None = None,
    corpus: Corpus | None = None,
    *,
    with_verifiers: bool = True,
    with_router_llm: bool = True,
) -> QueryPipeline:
    """Wire the full query pipeline.

    `with_verifiers=False` and `with_router_llm=False` exist for the ablation
    runner, which needs to measure what each stage was worth rather than assume
    it (§24.3).
    """
    settings = settings or load_settings()
    corpus = corpus or load_corpus(settings)

    catalog = build_catalog(settings.filings_dir)
    aliases = load_aliases(settings.data_dir / "company_aliases.yaml")
    router = DocumentRouter(
        catalog,
        aliases,
        top_k=settings.routing.top_k_filings,
        clarify_when_no_company=settings.routing.clarify_when_no_company,
        prefer_coverage_years=settings.routing.prefer_coverage_years,
    )

    return QueryPipeline(
        settings=settings,
        router=router,
        bm25=corpus.bm25,
        anchors=corpus.anchors,
        pages_by_doc=corpus.pages_by_doc,
        headers_by_page=corpus.headers_by_page,
        coverage_years=corpus.coverage_years,
        extractor=get_provider(settings, "extractor"),
        verifier_a=get_provider(settings, "verifier_a") if with_verifiers else None,
        verifier_b=get_provider(settings, "verifier_b") if with_verifiers else None,
        formula_book=FormulaBook(),
        router_llm=get_provider(settings, "router") if with_router_llm else None,
        composer=get_provider(settings, "composer"),
    )
