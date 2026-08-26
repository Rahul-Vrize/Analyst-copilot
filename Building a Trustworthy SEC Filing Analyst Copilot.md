# Building a Trustworthy SEC Filing Analyst Copilot

## Executive Summary

- **Primary Architecture**: The strongest practical design is a filing-local, structure-aware evidence system, not a generic chatbot: preserve EDGAR HTML and Inline XBRL, retrieve typed evidence, calculate with code, verify independently, then answer or abstain -> build the evidence and verification path before adding agentic behavior.
- **HTML Is an Advantage**: Inline XBRL makes one filing both human-readable and machine-readable, and tagged facts carry context such as definitions, periods, and units [executive_summary[0]] [1] -> retain the original DOM and XBRL metadata rather than converting the filing to PDF.
- **Structure Beats Blind Chunking**: On SEC financial reports, element-based structural chunks outperformed fixed and recursive alternatives while reducing the chunk count from 112,155 to 62,529 [executive_summary[1]] [18] -> index sections, rows, cells, and footnotes as typed objects with stable locations.
- **Retrieval Must Be Hybrid and Period-Aware**: A 2026 financial retrieval study reports that BM25 outperformed dense retrieval for precise numerical questions, while hybrid retrieval plus neural reranking reached Recall@5 of 0.816 in its experiment [executive_summary[2]] [17] -> use lexical retrieval and explicit company, form, period, segment, and unit filters before optional dense reranking.
- **Tables Need Executable Reasoning**: TAT-QA questions require operations such as arithmetic, comparison, sorting, and composition; TAGOP extracts relevant cells and spans before symbolic reasoning, yet still reports 58.0 F1 versus 90.8 for humans [executive_summary[3]] [6] -> normalize tables and execute formulas deterministically instead of asking an LLM to do arithmetic.
- **More Reasoning Is Not Automatically Safer**: Financial QA errors increase with reasoning-step count, especially through retrieval and multi-step arithmetic errors [executive_summary[4]] [7], and SEC-QA found that iterative CodeGen improved some retrieval tasks at roughly four times the LLM-call cost [executive_summary[5]] [19] -> route only genuinely complex questions into bounded multi-step plans.
- **Evidence Must Be Verified, Not Merely Cited**: Citation correctness asks whether a citation supports a statement, while citation faithfulness asks whether the model actually relied on the cited source; a 2025 study reports that up to 57% of citations lacked faithfulness [executive_summary[6]] [12] -> generate claims from an evidence ledger and have a separate verifier reconstruct and check them.
- **Abstention Requires Deliberate Data**: AbstentionBench finds that abstention remains unsolved and that reasoning fine-tuning can reduce abstention performance by 24% on average [executive_summary[7]] [4] -> create missing-period, wrong-segment, false-premise, incomplete-calculation, and ambiguous-unit negatives, then calibrate a conservative answer threshold.
- **Hackathon Scope Is a Feature**: Do not begin with PDF OCR, a graph database, full GraphRAG, an unbounded autonomous agent, fine-tuning, or a vector-database cluster. The high-value first build is a cached HTML/XBRL parser, a SQLite or DuckDB evidence store, BM25 plus metadata retrieval, a typed calculator, a bounded verifier, and an evaluation harness.

## Recommended Pipeline at a Glance

| Stage | Recommendation | Main benefit | Alternatives considered and rejected | Complexity |
|---|---|---|---|---|
| Ingestion | Download and preserve the accession HTML, filing metadata, Inline XBRL facts, taxonomy references, and original markup. Parse with an HTML DOM pipeline and Arelle for XBRL support. | No loss of table structure, semantic tags, units, periods, or exact source coordinates. | PDF conversion and OCR discard or distort HTML structure; plain text loses table coordinates. | Medium |
| Document representation | Build a typed tree of filing, item, section, paragraph, table, row, cell, footnote, list, and continuation objects. Add a fact ledger and explicit cross-reference edges. | Supports section-aware retrieval, table operations, parent expansion, and exact evidence. | Fixed token chunks are easy but mix periods, headers, and unrelated rows. | Medium |
| Storage | Use raw files plus SQLite or DuckDB as the source of truth; use FTS5 or equivalent BM25 search, relational table views, JSON metadata, and an optional small local vector index. | Fast, reproducible, inexpensive, and easy to inspect during a hackathon. | A graph database or distributed vector store adds operations without solving the core evidence problem. | Low to medium |
| Retrieval | Route queries by type, apply filing-local metadata filters, retrieve with BM25, optionally merge dense results with reciprocal-rank fusion, rerank a small candidate set, then expand to parent sections, neighboring rows, and footnotes. | Lexical matching protects numbers and terminology; structure and metadata protect against wrong-year and wrong-segment evidence. | Vector-only retrieval is vulnerable to semantically similar but period-wrong passages; long-context prompting is expensive and hard to audit. | Medium |
| Reasoning | Convert the question into evidence slots and typed operations. Use SQL or Python Decimal for arithmetic and a bounded subquestion plan for multi-hop questions. | Separates finding facts from computing and makes each answer reproducible. | Free-form chain of thought and unbounded agents introduce unnecessary calls and unsupported assumptions. | Medium |
| Verification | Independently check evidence-slot completeness, entailment, period, unit, dimension, formula, contradiction, and location. | Makes unsupported answers fail closed before generation. | A single LLM self-check is not sufficiently independent; citation insertion after generation can rationalize an answer. | Medium |
| Evidence | Return claim-level evidence IDs pointing to DOM ranges, table coordinates, XBRL fact metadata, and optionally deterministic rendered page numbers. | Satisfies the scoring rule for exact location and allows human audit. | Page-only citations are unstable for native HTML and do not identify a table cell or fact. | Medium |
| Abstention | Return exactly `Not found in this filing.` when required evidence is absent, contradictory, ambiguous, or not sufficiently localized. Calibrate on synthetic and hard negatives. | Converts the -1 penalty into a controlled selective-answering problem. | Always-answer prompting and confidence text do not provide a defensible refusal policy. | Medium |

The design is intentionally asymmetric: inexpensive deterministic mechanisms handle high-frequency lookup and arithmetic, while LLM reasoning is reserved for query planning, semantic synthesis, and difficult verification. This is more likely to maximize correct, provable answers than optimizing answer rate alone.

## Ingestion: Parse EDGAR HTML and Inline XBRL Without Loss

Start from the SEC accession package. Store the original filing URL, accession number, CIK, company name, form type, filing date, reporting period, and an immutable copy of the raw HTML. SEC EDGAR full-text search covers electronic filings back to 2001 and exposes filing metadata and document locations [ingestion_parse_edgar_html_and_inline_xbrl_without_loss[0]] [2] [ingestion_parse_edgar_html_and_inline_xbrl_without_loss[0]] [2]. The raw file should be content-addressed with a hash so that every parsed object can be reproduced after a parser update.

Do not render the filing to PDF as the canonical representation. Inline XBRL is specifically designed to make the same document human-readable and machine-readable [ingestion_parse_edgar_html_and_inline_xbrl_without_loss[1]] [1]. Its tagged facts include context such as definitions, reporting periods, and other metadata [ingestion_parse_edgar_html_and_inline_xbrl_without_loss[1]] [1]. Run a standards-aware XBRL parser over the original HTML. Arelle is a practical open-source option because it provides a Python API, command-line and service interfaces, and support for Inline XBRL 1.1, XBRL dimensions, taxonomies, and xBRL-JSON or CSV output [ingestion_parse_edgar_html_and_inline_xbrl_without_loss[2]] [13]. Store both the extracted fact and the source node from which it came. The XBRL fact is not a replacement for the displayed table: it is a typed, cross-checkable view of that table.

The HTML parser should retain the DOM hierarchy and annotate each object with a stable evidence locator. For every paragraph, heading, table, row, cell, footnote, and XBRL fact, store the accession, DOM element ID or XPath, character offsets in a canonical serialization, section path, and object hash. For table cells, additionally store table ID, row index, column index, row and column header paths, `rowspan` and `colspan` expansion, displayed text, normalized numeric value, unit, scale, sign, and footnote references. For XBRL facts, store QName, context ID, dimensions, period, unit, decimals, scale, sign, and the exact Inline XBRL element.

A parser such as sec-parser demonstrates the value of turning EDGAR HTML into a tree of semantic elements such as section titles, paragraphs, and tables [ingestion_parse_edgar_html_and_inline_xbrl_without_loss[3]] [16]. It also demonstrates extraction of table rows and numbers from an SEC filing [ingestion_parse_edgar_html_and_inline_xbrl_without_loss[3]] [16] [ingestion_parse_edgar_html_and_inline_xbrl_without_loss[3]] [16]. However, its repository says it is no longer maintained [ingestion_parse_edgar_html_and_inline_xbrl_without_loss[3]] [16], and it is not an SEC-endorsed location standard [ingestion_parse_edgar_html_and_inline_xbrl_without_loss[3]] [16]. Use the idea and test fixtures, not the package as an unquestioned production dependency.

Native HTML often has no authoritative page number. If the hackathon evaluator requires a page, render the exact canonical HTML with a pinned Chromium version, fixed viewport, font set, and print stylesheet. Persist a map from DOM object and character range to rendered page. Report DOM and table coordinates as the primary location and rendered page as a secondary convenience. This avoids the false precision of claiming that a dynamically rendered page is intrinsic to the SEC source.

### Case study: SEC-QA shows why ingestion and location are coupled

SEC-QA links metrics to a company, fiscal year, and source document so that systems can evaluate document and page retrieval as well as exact numeric-span extraction [ingestion_parse_edgar_html_and_inline_xbrl_without_loss[4]] [19] [ingestion_parse_edgar_html_and_inline_xbrl_without_loss[4]] [19]. Its authors converted HTML filings to PDF and parsed them into paragraphs, tables, and titles [ingestion_parse_edgar_html_and_inline_xbrl_without_loss[4]] [19]. That makes page evaluation possible, but it also illustrates a limitation for this project: conversion is useful for a derived display coordinate, not as the lossless source representation.

The implementation consequence is to maintain two identities for every answer: a semantic identity such as `revenue, FY2023, segment=Cloud` and a source identity such as `accession, DOM path, table ID, row, column, XBRL context`. A correct number with a wrong source coordinate receives zero under the project scoring, so these identities must never be collapsed.

## Representation and Storage: One Structural Tree, Two Queryable Views

Represent each filing as a typed document graph, but store it initially in ordinary relational tables rather than a graph database. The core tree is:

`Filing -> Part or Item -> Section -> Subsection -> Block`

where a block is a paragraph, list, table, table row, table cell, footnote, caption, or continuation. Each node has `parent_id`, `previous_id`, `next_id`, `section_path`, source locator, and normalized text. Add explicit edges for HTML hyperlinks, footnote references, XBRL contexts, taxonomy definitions, continuation chains, table-to-narrative references, and cross-referenced section labels. This is enough to follow relationships locally without paying the complexity of a general-purpose knowledge graph.

Create two denormalized query views. The first is a narrative view containing headings, paragraphs, lists, captions, and footnotes. The second is a financial fact and table view containing one record per displayed cell and one record per XBRL fact. A table row should carry the complete header path, for example `Revenue | North America | 2023`, rather than only the visible number. The fact ledger should preserve both raw and normalized values, because a displayed value of `1,234` can mean thousands, millions, dollars, shares, or a percentage depending on nearby headers and XBRL metadata.

For a hackathon, use a directory or object store for raw filings, SQLite for metadata and FTS5, and DuckDB for table analytics and reproducible SQL. A small local vector index is optional, not authoritative. Keep a JSON representation of the typed document and a relational representation of facts and cells. Every derived object should point back to a raw source hash and parser version. This makes parser changes testable and allows the team to inspect failures without a distributed stack.

The 2024 financial-report chunking study is directly relevant: it compares fixed, recursive, contextual, hybrid, and element-based structural approaches on SEC 10-K, 10-Q, and 8-K material [representation_and_storage_one_structural_tree_two_queryable_views[0]] [18]. Its element-based approach obtained the best reported retrieval and QA results while producing roughly half as many chunks and lowering indexing and query latency [representation_and_storage_one_structural_tree_two_queryable_views[0]] [18]. The result supports structure-aware storage, but not the conclusion that one universal chunk size is optimal. Tables, footnotes, and narrative blocks need different retrieval units.

### Case study: structural chunks versus token chunks

The element-based study used FinanceBench instances and SEC reports, recovering 80 documents and 141 questions in its evaluation sample [representation_and_storage_one_structural_tree_two_queryable_views[0]] [18]. The important engineering decision was not merely changing a chunk length. It was treating document elements as the units of retrieval, which prevents a table header from being separated from the values it qualifies.

For this project, a table row should be retrievable together with its table title, column headers, row labels, and nearby footnote, while a narrative paragraph should be retrievable with its section heading and immediate context. The parent section can be added to the prompt for interpretation, but the evidence citation should remain anchored to the smallest object that actually entails the claim.

## Retrieval: Hybrid, Routed, and Filing-Local

The first retrieval decision is scope, not embedding. Since each question is associated with a filing, default to searching only that filing. Filter by form, reporting period, company, segment, unit, and table or section type before semantic ranking. This prevents a model from finding a plausible answer in another year or another company and then treating it as evidence.

Use a lightweight query router with four paths:

1. **Fact lookup**: search the XBRL fact ledger and BM25 index for metric aliases, period, and unit.
2. **Table lookup or calculation**: search table titles, headers, row labels, and footnotes, then return whole candidate rows and their header paths.
3. **Narrative lookup**: search BM25 over heading-plus-block text, with an optional dense index for synonyms and paraphrases.
4. **Multi-hop synthesis**: decompose the question into slots, retrieve each slot independently, then expand only through local parents, neighboring rows, referenced footnotes, or explicit cross-links.

For general retrieval, merge BM25 and optional dense candidates with reciprocal-rank fusion, then apply a cross-encoder or LLM reranker to a small set. BM25 is particularly important for financial values, fiscal-year strings, segment names, and accounting terminology. A 2026 preprint evaluating 10 strategies over 23,088 queries and 7,318 documents reports that BM25 outperformed dense retrieval for its precise numerical setting, while hybrid retrieval plus neural reranking reported Recall@5 of 0.816 and MRR@3 of 0.605 [retrieval_hybrid_routed_and_filing_local[0]] [17]. The work is a useful directional result, not production proof: it is financial-only, predominantly numerical, uses whole-document retrieval assumptions, and notes reproducibility limitations [retrieval_hybrid_routed_and_filing_local[0]] [17].

Do not use vector search alone. SEC-QA reports that neural retrieval struggles when filings contain structurally similar language repeated across years [retrieval_hybrid_routed_and_filing_local[1]] [19]. In its Adobe example, several methods repeatedly retrieved the wrong fiscal year even when they found the right company and metric [retrieval_hybrid_routed_and_filing_local[1]] [19]. The fix is not simply a larger model. It is explicit period filtering, metadata-aware scoring, table-aware reranking, and a verifier that compares the requested period with the evidence period.

Hard negatives are essential. FinRank reports that manually selected comparable-filing negatives reduced retrieval accuracy by 13.0 to 20.5 percentage points relative to random negatives [retrieval_hybrid_routed_and_filing_local[2]] [26]. Use the project filings to create negatives from adjacent years, neighboring segments, similar metric names, and the same table in another company. A passage that mentions the right metric but the wrong period is a more valuable test than an unrelated paragraph.

### Case study: Adobe's wrong-year failure

SEC-QA's Adobe case is a concrete failure mode for a financial copilot: the retriever was attracted to the correct company and repeated metric language but returned the wrong fiscal year [retrieval_hybrid_routed_and_filing_local[1]] [19]. This is not a generic semantic-search error. It is an entity-period disambiguation failure caused by repeated reporting templates.

The recommended response is to score period agreement as a hard gate. If the question asks for FY2018, a FY2017 cell should not remain an answer candidate merely because its text is highly similar. If the user asks a trend question, multiple periods become required evidence slots; the system should then retrieve the ordered series deliberately rather than allowing the ranking model to choose an arbitrary nearby year.

### Retrieval option comparison

| Approach | What it is good at | Main failure mode in SEC QA | Decision |
|---|---|---|---|
| BM25 or other lexical search | Exact metric names, fiscal years, segment labels, numbers, accounting phrases | Misses paraphrases and implicit relationships | Mandatory first-stage retriever |
| Dense vector search | Synonyms, paraphrases, qualitative similarity | Wrong period, wrong segment, and semantically similar repeated passages | Optional complementary retriever, never sole authority |
| Hybrid plus reranking | Combines exact terms with semantic recall and can inspect richer context | More components and possible reranker bias | Recommended default after metadata filtering |
| Long-context whole filing | Broad context and fewer retrieval decisions | Cost, distraction, repeated years, weak exact-location control | Use only as a bounded fallback for unresolved synthesis |
| Full GraphRAG | Explicit relationships and multi-hop traversal | High construction cost; graph errors and incomplete edges become hidden failure modes | Use local typed edges, not a general graph database |
| Unbounded agent | Can iteratively search and repair plans | Variable cost, non-reproducibility, and more opportunities to hallucinate | Replace with a bounded plan and fixed iteration budget |

The table implies a practical order: metadata and lexical retrieval first, optional dense recall second, reranking third, local expansion fourth. Do not ask an expensive model to discover a constraint that the filing metadata already provides.

## Reasoning and Calculations: Evidence Slots, SQL, and Deterministic Programs

Represent the question as an `AnswerPlan`, not as an unrestricted conversational prompt. A plan should contain `question_type`, `requested_periods`, `entities`, `dimensions`, `required_evidence_slots`, `operations`, `output_format`, and `abstention_conditions`. Each evidence slot has a target such as `capex FY2018`, `revenue FY2018`, or `segment growth excluding acquisitions`. The system cannot answer until every required slot is either supported or explicitly marked missing.

Use a deterministic execution path for numeric questions. Convert retrieved facts into typed values with period, unit, scale, sign, and dimension metadata. Execute addition, subtraction, multiplication, division, percentage change, CAGR, ranking, and comparisons in Python Decimal or DuckDB. Store the formula, operands, normalized units, and source evidence IDs in the answer record. The LLM may identify the formula, but it should not be the calculator.

This is strongly supported by financial QA research. FinQA was authored around deep questions over financial reports and supplies gold reasoning programs for explainability [reasoning_and_calculations_evidence_slots_sql_and_deterministic_programs[0]] [5]. TAT-QA includes hybrid table and text questions requiring arithmetic, comparison, sorting, and composition; TAGOP extracts relevant cells and text spans before symbolic reasoning [reasoning_and_calculations_evidence_slots_sql_and_deterministic_programs[1]] [6]. A 2024 study covering TATQA, FinQA, ConvFinQA, and Multihiertt reports that the datasets provide executable programs and that errors arise from both retrieval and multi-step arithmetic; it recommends external calculation tools for calculation errors [reasoning_and_calculations_evidence_slots_sql_and_deterministic_programs[2]] [7] [reasoning_and_calculations_evidence_slots_sql_and_deterministic_programs[2]] [7].

For qualitative questions such as whether a company is capital-intensive, do not invent a universal definition. Require the plan to identify the operating definition used in the answer, for example capex relative to revenue over a stated period, and retrieve every input. If the filing supports capex and revenue but not the comparative threshold or definition needed for a categorical conclusion, return the refusal or give a qualified numerical comparison rather than asserting an unsupported label.

For multi-hop questions, perform bounded iterative retrieval. Decompose into two to five atomic slots, retrieve them in parallel when independent, join them on company, period, segment, and unit, and allow at most a small number of expansions through explicit document links and parent nodes. A question such as which segment dragged down growth excluding M&A may require management's reported growth, acquisition or divestiture adjustments, and segment-level results. The answer plan should record those as separate evidence slots and show the subtraction or comparison.

TableRAG is a useful model for this design: it separates offline database construction from online iterative reasoning, combines query decomposition with text retrieval and SQL programming, and executes intermediate answers [reasoning_and_calculations_evidence_slots_sql_and_deterministic_programs[3]] [15]. FinAgent-RAG similarly uses a query decomposer, adaptive retriever, chain-of-thought and program-of-thought reasoners, a strategy router, and a self-verifier [reasoning_and_calculations_evidence_slots_sql_and_deterministic_programs[4]] [21]. Its reported Recall@5 rises from 69.7 to 82.3 over three iterations [reasoning_and_calculations_evidence_slots_sql_and_deterministic_programs[4]] [21], but the results are author-reproduced baselines [reasoning_and_calculations_evidence_slots_sql_and_deterministic_programs[4]] [21] and therefore should guide architecture rather than determine a production claim.

### Case study: TAGOP and executable table reasoning

TAGOP made a specific design choice: extract the cells and text spans that answer a TAT-QA question, then apply symbolic reasoning instead of relying on a language model to infer the result from a large table [reasoning_and_calculations_evidence_slots_sql_and_deterministic_programs[1]] [6]. It improved over prior reported systems but remained well below human performance, which exposes the remaining difficulty in selecting the right cells and preserving their context.

The lesson for the Analyst Copilot is to separate three errors that are often conflated: retrieving the wrong table, selecting the wrong row or column, and executing the wrong operation. The evidence ledger and deterministic calculator make these errors observable. A wrong result should name the failed slot or formula rather than producing an opaque answer.

## Verification and Evidence: Prove the Answer Before Generation

Use an evidence-first contract. The reasoning service should output a structured candidate such as:

```json
{
  "status": "supported",
  "claims": [
    {
      "text": "...",
      "evidence_ids": ["cell-123", "paragraph-456"],
      "formula": "...",
      "value": "..."
    }
  ],
  "missing_slots": [],
  "locations": []
}
```

The final language model should receive only the question, the answer plan, the selected evidence, the formulas, and the verifier result. It should not be allowed to add facts from general knowledge or silently fill missing slots. If the candidate is not supported, the renderer should output exactly `Not found in this filing.`

Verification should be an independent stage with deterministic checks first and model checks second. Deterministic checks include: every required slot has an evidence ID; every evidence object belongs to the requested accession; period and fiscal-year fields agree; company and segment dimensions agree; units and scales are compatible; table headers are included; formulas recompute; percentages have the correct denominator; and every cited location exists in the stored source hash. Also check for contradiction, such as a narrative sentence saying a measure is not disclosed while a proposed answer claims a value.

The verifier then asks focused questions: Does each claim follow from its cited evidence? Does the evidence support the exact period and dimension? Is the answer using a definition not present in the filing? Does the citation point to the smallest sufficient span or cell? This is not the same as asking the generator to add citations at the end. Citation correctness checks support, while citation faithfulness checks whether the model actually relied on the cited documents; a study reports that up to 57% of citations lacked faithfulness [verification_and_evidence_prove_the_answer_before_generation[0]] [12]. An evidence-first pipeline reduces this post-hoc citation problem.

For a qualitative synthesis, return claim-level evidence rather than a single citation attached to a paragraph. For a calculation, return operands, formula, result, and source location for every operand. For a ranking, return the complete compared set or explain why the filing contains only a partial set. For a cross-reference, return both the referring passage and the target footnote or table.

The exact-location record should contain `accession`, `filing_url`, `section_path`, `dom_id_or_xpath`, `char_start`, `char_end`, `table_id`, `row`, `column`, `xbrl_fact_id`, and optional `rendered_page`. The UI is outside scope, but the API should expose the original text or cell value for each evidence ID so an evaluator can check it directly.

### Case study: citation correctness is not enough

The citation-faithfulness study distinguishes a supported citation from a citation that was merely attached after an answer had already been generated [verification_and_evidence_prove_the_answer_before_generation[0]] [12]. This distinction matters especially in filings because a nearby table can support a similar but not identical number, period, or segment.

The operational decision is therefore to prohibit free-form finalization. The generator proposes a claim only after the retrieval and calculation layers have produced evidence IDs. The verifier can reject the proposal, but it cannot manufacture replacement evidence. This creates a fail-closed path that is more appropriate for a -1 penalty than a single-model confidence score.

## Abstention and Evaluation: Make "Not Found" a First-Class Result

The 136 practice questions are all answerable, so accuracy on them cannot measure whether the system knows when not to answer. Create a separate negative set before tuning prompts or thresholds. Use categories from abstention research: underspecified questions, false presuppositions, nonsensical requests, modality-limited requests, and out-of-database questions [abstention_and_evaluation_make_not_found_a_first_class_result[0]] [8]. AbstentionBench also distinguishes unknown, underspecified, false-premise, subjective, and outdated cases [abstention_and_evaluation_make_not_found_a_first_class_result[1]] [4].

Construct filing-local negatives rather than only unrelated questions. Useful examples include:

- Ask for a fiscal year absent from the filing.
- Substitute a neighboring segment or a different company.
- Ask for a number that appears in another filing but not the selected filing.
- Ask for a causal conclusion when the filing gives only correlation or descriptive data.
- Remove one operand from a multi-step calculation.
- Create an ambiguous unit or scale by asking without the table header.
- Ask for a comparison whose denominator is not disclosed.
- Use a false premise such as claiming the filing reports a metric that it does not contain.
- Use a cross-reference whose target is absent or contradictory.

Generate candidates with controlled transformations, but manually review a meaningful sample. FinRank provides a useful precedent: it includes manually selected hard negatives from comparable filings and gold support passages, and it reports that hard negatives are much more difficult than random negatives [abstention_and_evaluation_make_not_found_a_first_class_result[2]] [26] [abstention_and_evaluation_make_not_found_a_first_class_result[2]] [26]. Its limitations also matter: it is small and skewed, has a single annotation with sampled review, and represents tables and figures as text rather than multimodal objects [abstention_and_evaluation_make_not_found_a_first_class_result[2]] [26]. Treat it as a design reference, not a universal estimate of deployment performance.

Calibrate abstention on a held-out set split by company and filing, not just by question. Define a system utility that rewards a correct answer with correct evidence, assigns zero to the exact refusal, and assigns negative utility to unsupported answers. Choose the answer threshold on the validation set to minimize false answers under a target coverage level. Report risk-coverage curves, selective accuracy, false-answer rate on negatives, refusal rate, and the fraction of refusals caused by each failed check.

UAEval4RAG shows why a retrieval score alone is insufficient: a vector system without a reranker reported high answerable correctness but only 49.0% acceptable ratio on unanswerable questions, while stronger reranking improved rejection in some settings at a cost to answerable correctness [abstention_and_evaluation_make_not_found_a_first_class_result[0]] [8]. This is a real product tension, not a reason to always add a reranker. Evaluate answerable and unanswerable behavior jointly.

AbstentionBench reports that increasing reasoning can worsen abstention, including a 24% average degradation from reasoning fine-tuning and hallucinations when context is missing [abstention_and_evaluation_make_not_found_a_first_class_result[1]] [4]. The copilot should therefore use a smaller, more conservative path for simple lookup and require stronger evidence coverage for complex reasoning. The correct refusal is not a low-confidence explanation; it is the exact required string.

### Evaluation matrix

| Metric family | What to measure | Why it matters |
|---|---|---|
| End-to-end utility | Correct answer plus correct evidence, exact refusal, wrong-answer penalty | Matches the hackathon objective directly |
| Answer correctness | Exact numeric match, normalized numeric match, qualitative label accuracy, formula result | Separates answer generation from location quality |
| Evidence correctness | Evidence passage or cell exact match, section match, DOM location match, page match if required | A right answer with a wrong location scores zero |
| Retrieval | Recall@k, MRR, nDCG, slot recall, period and dimension accuracy | Finds whether failure occurs before reasoning |
| Calculation | Operand accuracy, unit compatibility, formula accuracy, recomputation success | Exposes arithmetic and scale errors |
| Abstention | False-answer rate, negative selective accuracy, risk-coverage, refusal reason | Measures safety under missing evidence |
| Efficiency | Ingestion time, indexing time, query latency, LLM calls, token cost | Enforces the 10-minute new-filing constraint |

SEC-QA's separation of document/page retrieval and exact numeric-span extraction is a useful template for this evaluation design [abstention_and_evaluation_make_not_found_a_first_class_result[3]] [19] [abstention_and_evaluation_make_not_found_a_first_class_result[3]] [19]. FinDER also evaluates retrieval and generation separately with context precision, correctness, and faithfulness measures [abstention_and_evaluation_make_not_found_a_first_class_result[4]] [24].

## What the Research Says About Benchmarks and New Methods

| Resource or system | Evidence base and capability | What it teaches this project | Caution |
|---|---|---|---|
| FinQA | Financial reports, expert-written deep questions, and gold reasoning programs [what_the_research_says_about_benchmarks_and_new_methods[0]] [5] | Store executable reasoning traces and test multi-step arithmetic | Benchmark performance is not the same as filing-local provenance |
| TAT-QA and TAGOP | Real financial reports with mixed table and text questions and symbolic cell/span reasoning [what_the_research_says_about_benchmarks_and_new_methods[1]] [6] | Tables require structure plus execution, not paragraph retrieval alone | Human performance remains far higher than reported systems |
| SEC-QA | 10-K, 10-Q, and 8-K data over 18 S&P 500 companies and 10 metrics; tests multi-document and page retrieval [what_the_research_says_about_benchmarks_and_new_methods[2]] [19] | Period-aware retrieval and location scoring are essential | The dataset is semi-automatically generated and has limited human annotation [what_the_research_says_about_benchmarks_and_new_methods[2]] [19] |
| Financial report chunking study | SEC filings and FinanceBench evaluation comparing structural chunking methods [what_the_research_says_about_benchmarks_and_new_methods[3]] [18] | Element-based units can improve both QA and efficiency | Results depend on chunking and evaluation setup |
| FinRank | 1,185 manually authored records over 10-K and 10-Q filings from 22 companies, with support passages, query rewrites, and hard negatives [what_the_research_says_about_benchmarks_and_new_methods[4]] [26] [what_the_research_says_about_benchmarks_and_new_methods[4]] [26] | Test easy lookup, comparison, and multi-section synthesis separately | Small, skewed, single-annotation and text-only table limitations [what_the_research_says_about_benchmarks_and_new_methods[4]] [26] |
| FinDER | 5,703 expert-annotated query-evidence-answer triplets from raw HTML for 490 companies [what_the_research_says_about_benchmarks_and_new_methods[5]] [24] [what_the_research_says_about_benchmarks_and_new_methods[5]] [24] | Evidence selection and retrieval-generation separation are realistic targets | Preprint results and broad categories do not replace project-specific tests |
| TableRAG | Offline table database plus iterative query decomposition, text retrieval, SQL, and intermediate answers [what_the_research_says_about_benchmarks_and_new_methods[6]] [15] | A database-plus-reasoning loop is a practical alternative to generic RAG | Reported gains are model- and benchmark-dependent [what_the_research_says_about_benchmarks_and_new_methods[6]] [15] |
| FinAgent-RAG | Routed iterative retrieval and self-verification with reported Recall@5 gains [what_the_research_says_about_benchmarks_and_new_methods[7]] [21] [what_the_research_says_about_benchmarks_and_new_methods[7]] [21] | Use routing and bounded iteration selectively | Author-reproduced baselines and residual multi-table errors limit confidence [what_the_research_says_about_benchmarks_and_new_methods[7]] [21] [what_the_research_says_about_benchmarks_and_new_methods[7]] [21] |

The most reliable conclusions are architectural rather than leaderboard-specific. Independent benchmark evidence consistently points to structure, retrieval, and executable reasoning as separate problems. The newest preprints are valuable for ideas such as corrective retrieval, query decomposition, hard negatives, and routing, but their reported gains are self-reported and often use narrow or synthetic settings. For example, the 2026 BM25-to-Corrective-RAG study covers 23,088 queries but explicitly describes financial and numerical scope limitations [what_the_research_says_about_benchmarks_and_new_methods[8]] [17] [what_the_research_says_about_benchmarks_and_new_methods[8]] [17].

The project dataset is unusually valuable because it has exact evidence locations. Preserve that property. Do not reduce the evaluation to answer strings or use an LLM judge as the primary score. Use the supplied evidence passage and page as a gold source, and add DOM and table-cell gold labels when possible.

## Hackathon Build Order: What I Would Build First

### Phase 1: Make evidence locations deterministic

Build a loader for the 78 filings that stores raw HTML, metadata, content hash, canonical serialization, DOM object IDs, section paths, and rendered-page maps if pages are mandatory. Create a small inspection script that prints a table cell, its header path, its XBRL metadata, and its exact locator. Before any LLM work, verify that every one of the 136 gold answers can be represented by a stable evidence ID.

### Phase 2: Build the typed table and fact layer

Run Arelle or an equivalent XBRL parser, expand HTML tables with row and column spans, normalize numbers and units, and store facts and cells in SQLite or DuckDB. Implement a deterministic lookup API such as:

`find_facts(metric_aliases, period, dimensions, unit)`

and:

`find_table_rows(title_terms, header_terms, row_terms)`

Return the source locator with every result. This layer should solve simple lookups such as FY2018 capex without an LLM whenever the fact is tagged or clearly present in a table.

### Phase 3: Add filing-local hybrid retrieval

Implement BM25 over heading-plus-block text, table titles, header paths, row labels, and footnotes. Add metadata filters before ranking. If time permits, add a small dense index and a cross-encoder reranker, but compare it against BM25 rather than assuming it improves the score. Add hard negatives from adjacent periods and segments immediately.

### Phase 4: Add the bounded planner and calculator

Implement the `AnswerPlan` schema, evidence slots, SQL or Decimal operations, formula provenance, and a maximum number of retrieval iterations. The first planner can be a prompt-based classifier with deterministic fallbacks. It should choose lookup, table calculation, narrative synthesis, or bounded multi-hop, not freely invent a tool sequence.

### Phase 5: Add the verifier and abstention policy

Implement deterministic gates first, then a separate verifier prompt. The verifier should reject missing slots, wrong periods, unit mismatches, unsupported causal language, and unlocalized claims. Add synthetic negatives, calibrate the threshold on held-out companies, and report the full evaluation matrix. This phase is more important than adding another generator model because the scoring function penalizes confident errors.

### Practical 10-minute budget

Cache parsed documents and XBRL output by accession hash. Parallelize independent table parsing and indexing work. Avoid reparsing the same filing on every query. Set a query budget such as one router call, a few parallel retrieval calls, one bounded reasoning call, and one verifier call; use deterministic lookup without an LLM where possible. If the verifier cannot establish support within the budget, refuse rather than continue indefinitely.

### What to avoid initially

- PDF conversion or OCR as the primary path. The source is structured EDGAR HTML, and Inline XBRL already provides machine-readable facts [hackathon_build_order_what_i_would_build_first[0]] [1].
- Vector-only retrieval, because repeated financial language creates wrong-year failures [hackathon_build_order_what_i_would_build_first[1]] [19] [hackathon_build_order_what_i_would_build_first[1]] [19].
- A full graph database. Store explicit local edges first; only add graph infrastructure if evaluation shows that cross-reference traversal is the bottleneck.
- An unbounded autonomous agent. More iterations can raise recall but also raise latency, cost, and the number of opportunities to introduce unsupported facts [hackathon_build_order_what_i_would_build_first[1]] [19].
- Fine-tuning before building negatives and evidence labels. The current dataset is positive-only, so calibration and error taxonomy are the immediate bottlenecks.
- LLM arithmetic or untraceable chain-of-thought as the source of truth. Execute formulas and expose operands instead.
- Whole-filing long-context prompting as the default. FinDER reports that whole-document feeding is ineffective because of cost and latency [hackathon_build_order_what_i_would_build_first[2]] [24].
- A large production vector or graph cluster. It does not improve a missing period filter, a bad table parser, or an absent evidence locator.

## Synthesis

The research points to a hybrid system, but not the usual meaning of hybrid RAG. The essential combination is structured extraction, lexical retrieval, optional semantic retrieval, deterministic execution, bounded reasoning, and independent verification. Each component compensates for a different failure mode.

| Candidate architecture | Mechanism | Scope and time horizon | Trade-off | Final judgment |
|---|---|---|---|---|
| Structured fact and table lookup | XBRL and DOM parsing followed by typed lookup | Immediate, simple facts and many numeric questions | Cannot by itself explain qualitative relationships or implicit cross-references | Foundation and fastest path |
| Hybrid filing-local RAG | BM25 plus optional dense retrieval, metadata filtering, reranking, and parent expansion | Most narrative and table retrieval within one filing | Requires index and reranker tuning; still needs verification | Default retrieval layer |
| Programmatic table QA | Evidence slots, SQL or Decimal formulas, symbolic operations | Calculations, comparisons, trends, and rankings | Requires robust table normalization and operation planning | Default for numeric reasoning |
| Bounded agentic QA | Query decomposition, limited iterative retrieval, local edge following, one verifier | Multi-section questions and difficult cross-references | More latency and cost; needs hard stopping rules | Add selectively after the baseline |
| Long-context whole-filing QA | Send large portions or the full filing to an LLM | Emergency fallback for unresolved synthesis | Expensive, distractible, weak exact-location discipline | Fallback only, not the architecture |
| Full GraphRAG | Build a general entity-relation graph and traverse it | Large collections and repeated cross-document exploration | High engineering cost and graph incompleteness | Avoid for the hackathon |
| PDF or multimodal pipeline | Render, OCR, and use page or visual models | Scanned or image-native reports | Unnecessary loss and latency for HTML EDGAR filings | Avoid as the primary path |

There are four important tensions. First, answer coverage and abstention conflict: more retrieval and reasoning can find difficult answers, but reasoning models may hallucinate when context is missing, and higher reasoning budgets can worsen abstention [synthesis[0]] [4] [synthesis[0]] [4]. The answer is routing, not a universal setting. Simple lookup should be conservative and deterministic; multi-hop synthesis should require more evidence, not merely more tokens.

Second, reranking and rejection can conflict with answer correctness. UAEval4RAG reports settings where stronger reranking improved unanswerable rejection but reduced answerable correctness [synthesis[1]] [8]. Evaluate reranking against the project utility, not Recall@k alone. A reranker that rejects everything is safe but unhelpful; one that answers everything maximizes coverage but incurs -1 outcomes.

Third, structure and semantic flexibility pull in different directions. Exact DOM and table units preserve provenance but may miss paraphrased questions. Dense retrieval helps with paraphrase but can blur period and dimension distinctions. The solution is to use dense search as a recall supplement while keeping lexical, metadata, and typed evidence checks authoritative.

Fourth, new agentic systems report strong gains but often spend more calls and rely on benchmark-specific baselines. FinAgent-RAG reports that a router reduced calls from 5.83 to 3.42 in one setting, but its router accuracy was not uniformly better than no router [synthesis[2]] [21] [synthesis[2]] [21]. SEC-QA similarly reports the cost of iterative planning [synthesis[3]] [19]. The practical design is therefore a bounded planner with observable state, not an autonomous agent with indefinite exploration.

The final recommendation is:

`EDGAR HTML and Inline XBRL -> typed DOM/tree plus fact ledger -> SQLite or DuckDB with BM25 and optional vectors -> filing-local routed hybrid retrieval -> evidence-slot plans plus deterministic SQL/Decimal calculations -> independent evidence and formula verification -> claim-level DOM/table/XBRL locators plus optional rendered pages -> calibrated exact refusal: Not found in this filing.`

This architecture is strong because it makes the most dangerous mistakes difficult: wrong-year retrieval is filtered, arithmetic is executable, citations are generated from source IDs, and missing evidence terminates in an explicit refusal. It also fits the 10-minute ingestion limit and can be built incrementally without committing the team to infrastructure that the evaluation may not reward.

## References

1. *SEC.gov | Inline XBRL*. https://www.sec.gov/data-research/structured-data/inline-xbrl
2. *SEC.gov | EDGAR Full Text Search*. https://www.sec.gov/edgar/search
3. *FinanceBench: A New Benchmark for Financial Question Answering*. http://arxiv.org/html/2311.11944
4. *AbstentionBench: Reasoning LLMs Fail on Unanswerable Questions*. https://arxiv.org/html/2506.09038v1
5. *FinQA: A Dataset of Numerical Reasoning over Financial Data - ACL Anthology*. https://aclanthology.org/2021.emnlp-main.300
6. *TAT-QA: A Question Answering Benchmark on a Hybrid of Tabular and Textual Content in Finance - ACL Anthology*. https://aclanthology.org/2021.acl-long.254
7. *aclanthology.org*. https://aclanthology.org/2024.findings-acl.231.pdf
8. *Unanswerability Evaluation for Retrieval Augmented Generation*. https://arxiv.org/html/2412.12300v1
9. *SEC-QA: A Systematic Evaluation Corpus for Financial QA*. https://arxiv.org/html/2406.14394v1
10. *MultiFinRAG: An Optimized Multimodal Retrieval-Augmented Generation (RAG) Framework for Financial Question Answering*. https://arxiv.org/html/2506.20821
11. *GitHub - docling-project/docling: Get your documents ready for gen AI · GitHub*. https://github.com/docling-project/docling
12. *Correctness is not Faithfulness in Retrieval Augmented Generation Attributions | Proceedings of the 2025 International ACM SIGIR Conference on Innovative Concepts and Theories in Information Retrieval (ICTIR)*. http://dl.acm.org/doi/10.1145/3731120.3744592
13. *GitHub - Arelle/Arelle: Arelle open source XBRL platform · GitHub*. https://github.com/Arelle/Arelle
14. *SEC.gov | EDGAR Application Programming Interfaces (APIs)*. http://sec.gov/search-filings/edgar-application-programming-interfaces
15. *TableRAG: A Retrieval Augmented Generation Framework for Heterogeneous Document Reasoning*. https://arxiv.org/html/2506.10380
16. *GitHub - alphanome-ai/sec-parser: Parse SEC EDGAR HTML documents into a tree of elements that correspond to the visual (semantic) structure of the document. · GitHub*. https://github.com/alphanome-ai/sec-parser
17. *From BM25 to Corrective RAG: Benchmarking Retrieval Strategies for Text-and-Table Documents*. https://arxiv.org/html/2604.01733v1
18. *Financial Report Chunking for Effective Retrieval Augmented Generation*. https://arxiv.org/html/2402.05131v1
19. *aclanthology.org*. https://aclanthology.org/2025.finnlp-2.15.pdf
20. *Corrective Retrieval Augmented Generation*. https://arxiv.org/html/2401.15884
21. *Agentic Retrieval-Augmented Generation for Financial Document Question Answering*. https://arxiv.org/html/2605.05409v1
22. *Retrieval-Augmented Generation: A Comprehensive Survey of Architectures, Enhancements, and Robustness Frontiers*. https://arxiv.org/html/2506.00054v1
23. *FinSage: A Multi-aspect RAG System for Financial Filings Question Answering*. https://arxiv.org/html/2504.14493v3
24. *FinDER: Financial Dataset for Question Answering and Evaluating Retrieval-Augmented Generation*. https://arxiv.org/html/2504.15800v3
25. *FinRAGBench-V: A Benchmark for Multimodal RAG with Visual Citation in the Financial Domain*. https://arxiv.org/html/2505.17471
26. *FinRank: An Evidence-Grounded Benchmark for Financial Question Answering and Retrieval over SEC Filings*. https://arxiv.org/html/2608.07400
