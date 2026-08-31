# Implementation Summary

Everything built in this session, why it exists, and what it measured.

Companion documents:
* **[BUILD_LOG.md](BUILD_LOG.md)** — the measurements and the defects found by running it
* **[APPROACH_NOTE.md](APPROACH_NOTE.md)** — the one-page submission note
* **[README.md](README.md)** — how to run it from scratch
* `../IMPLEMENTATION_PLAN.md` — the build spec this implements (§ references below)

---

## 0. Starting point → end state

| | Before | After |
|---|---|---|
| Database | none applied | **12 tables on Azure Postgres**, `vector(1024)`, hnsw, idempotent |
| Corpus | not ingested | **78/78 filings**, 8,389 pages, 29,412 typed cells |
| Storage | SQLite + DuckDB | **PostgreSQL + pgvector** (D7) |
| Document routing | did not exist | **top-1 95.6% / top-4 98.5%** |
| Query pipeline | filing-local stub | **full spine**, end to end on live models |
| Product surface | none | **FastAPI (6 endpoints) + Streamlit**, all four graded controls |
| Tests | 3 failing on superseded code | **207 passing**, 6 skipped |

---

## 1. Foundation

### `config.py` — the only environment reader (§24.1, §24.4)
Typed `Settings` from `config.yaml` + `.env`. Every tunable — model bindings,
reasoning budgets, retrieval `k`, gate toggles, thresholds — resolves here, so a
different corpus is a config change rather than a code change.

It validates at startup and **fails fast**. It immediately caught a live
inconsistency: `config.yaml` declared `models.embeddings.dimensions: 1536` while
`.env` said `1024` and the schema is `vector(1024)`. That mismatch fails *every*
page insert — at the *end* of a long ingest.

### `ids.py` — derived identifiers
`3M_2018_10K#p59`, `…#p59.t2.r4c3`. Ids come from position, never random, so a
re-ingest overwrites rather than duplicating and a page id reads as a citation.

### `migrations/001_init.sql` — applied and verified
Needed **VECTOR, PG_TRGM and UNACCENT** allow-listed in the Azure
`azure.extensions` server parameter. The earlier "extensions are available"
check read `pg_available_extensions`, which reports what the server *could*
install — not what it is *permitted* to. Re-running the migration exits 0.

---

## 2. Ingestion (§21.1, §16.3, §14.2)

| Module | What it does |
|---|---|
| `ingest/pages.py` | the validated page splitter — pages are the retrieval **and** citation unit |
| `ingest/edgar.py` | EDGAR full-submission envelope handling |
| `ingest/blocks.py` | typed blocks (heading/paragraph/list/footnote/table), prev/next linked |
| `ingest/tables.py` | the 7-step table pipeline ending in the alignment gate |
| `ingest/sections.py` | the four-signal section tree + statement anchors |
| `ingest/catalog.py` | filing metadata incl. `coverage_years` |
| `ingest/pipeline.py` | orchestrates S1–S12, writes visible ingest status |

### Three measured corrections
1. **An `<hr>` inside a `<table>` is a cell rule, not a page seam.**
   `MICROSOFT_2016_10K` has 1,836 `<hr>`, **1,728 inside a `<td>`**. Treating
   them as seams split it into **343 pages instead of 107**.
2. **Three filings are EDGAR full submissions** wrapping several concatenated
   `<html>` documents; lxml parses only the first, so we were reading ~3.5k
   chars of navigation chrome and **none of the filing**. This also explained
   the recorded "3 filings have no page-break markers" — they have none because
   we were never reading their content. Corrected: **all 78 have markers.**
3. **Two symmetric header-extraction bugs**, 30 tables each way on Microsoft
   alone: `"Year Ended June 30,"` counted as a column label (it is a spanning
   descriptor) and `"Percentage Change"` *not* counted (it is a real column).
   Alignment rose 21.9% → 31.2%.

### The alignment gate — fail closed
`len(header_tokens) == len(value_columns)` decides whether typed `table_cells`
exist at all. Where it fails there is markdown and **no typed facts**: a typed
fact exists only when its provenance is provable.

Verified on the canonical case — gold `financebench_id_03029` (3M FY2018 capex,
`$1577.00`) maps to derived page **61**, whose typed cell reads
`Purchases of property, plant and equipment (PP&E) | 2018 | -1577 | millions`.

---

## 3. Storage (§24.2)

`storage/db.py` (the only module opening a connection) and
`storage/repository.py` (the only module writing SQL). Every write is an upsert
on a derived id, so ingest is idempotent and a re-upload overwrites cleanly.

---

## 4. Retrieval (§22.3, §4, D10)

| Module | Role |
|---|---|
| `retrieval/anchors.py` | statement-title regexes — **73.2% recall alone, no LLM** |
| `retrieval/bm25.py` | in-memory BM25 over the composite lexical field |
| `retrieval/fusion.py` | RRF (adapted from `sec-rag-analyst`) |
| `retrieval/rerank.py` | Cohere, recall-oriented posture, behind an ablation flag |
| `retrieval/assemble.py` | neighbour expansion → RRF → rerank → token budget |

### Two fixes that produced the plan's numbers
* **Hints must RANK, not FILTER.** Filtering anchors to hinted statements gave
  68.5% from 15.5 pages; keeping every statement page and merely ordering by
  hint gives **73.2% from 26.6 pages**. Anchors are a recall stage; verification
  is the precision gate.
* **BM25 `k` must be allocated per candidate filing.** One global top-20 over
  the router's 4 candidates lets a wrong filing crowd out the right one:
  **74.8% → 80.3%**.

| Configuration | Plan | Measured |
|---|---|---|
| anchors only (oracle doc) | 73.0% | **73.2%** |
| BM25@20 only (oracle doc) | 18.3% raw | **46.5%** |
| anchors + BM25@20 (oracle) | 85.7% | **85.0%** |
| anchors + BM25@20 (top-4) | 81.0% | **80.3%** |

---

## 5. Query pipeline (§18.1, §20.2, §20.5)

| Module | Role |
|---|---|
| `query/router.py` | deterministic document router — **no LLM** |
| `query/extract.py` | evidence slots with verbatim quotes, never prose |
| `query/compute.py` | AST-whitelisted `Decimal` calculator |
| `query/formula_book.py` + `.yaml` | 25 metrics / 23 operands; also the us-gaap concept map |
| `query/gates.py` | G1, **G1b**, G2–G7 |
| `query/compose.py` | the ANSWER stage, switching on answer shape |
| `query/pipeline.py` | **the spine** — the only place stage order lives |

### The router
Company alias (hard gate), fiscal year with "latest wins", form/quarter, 8-K
event-date proximity. **top-1 95.6% / top-4 98.5%.** The two misses name no
company and take the clarify path — free, where a wrong document is −1.

### The calculator
The model may choose *which* formula; it never does arithmetic. A formula string
is parsed to an AST and every node outside the whitelist is rejected — a
level-3 formula is written by an LLM, so it may compute a number and nothing
else.

### The gates
**G1 — the quote must appear verbatim on the cited page — is the single most
important piece of code here.** It makes an invented figure or fabricated
citation structurally impossible, for the cost of one string search.

**G1b was added from evidence**: the extractor quoted the line-item *label* with
no figures. Every gate passed and both verifiers then rejected it — correctly,
but a tier too late. G1b checks deterministically that the reported figure
appears inside its own quote.

**G4 was scoped from evidence**: enforcing `missing_slots == []` for narrative
answers abstained on questions the evidence fully supported. It now applies to
answers built from operands; for narrative answers the composer and verifiers
decide.

---

## 6. The ANSWER stage — the largest gap running it revealed

`_render_answer` returned the first numeric slot. But **84/136 gold answers
(62%) are non-numeric**.

Asked *"which segment has dragged down 3M's overall growth in 2022?"* (gold:
*"The consumer segment shrunk by 0.9% organically"*) the pipeline answered
**`8,902`**. Every deterministic gate passed, because the figure was correctly
quoted and correctly located. It was simply **not an answer to the question** —
a relevance failure the gates structurally cannot catch.

`query/compose.py` composes the answer in the shape the question asks for.
Even a scalar goes through it: bare `1,577` was rejected by verifier A for
stating neither unit nor period, while *"$1,577 million in FY2018 for purchases
of property, plant and equipment (PP&E)"* passes all gates and both verifiers
and scores **+1**.

---

## 7. The LLM seam (§22.7)

`llm/base.py` (protocol, `LLMResponse`, `LLMTruncated`, `LLMRateLimited`,
`with_retry`), adapters for `azure_openai` / `anthropic_foundry` /
`cohere_foundry`, `registry.py`, `schemas.py`, and **10 versioned prompts**.

* **No stage imports a vendor SDK; every call is structured.** Swapping verifier
  B to a real second family stays three lines in `.env`, and the Anthropic
  adapter is written and imported by tests so the path is real, not designed.
* **A prompt and its schema must declare the same fields.** Measured: the router
  prompt asked for `form_types`/`periods` while the schema omitted them, and
  gpt-5-mini satisfied both by cramming the missing fields into a string
  (`metric_name: "revenue','form_types':[],"`). Schemas now live beside the
  prompt loader with a test enforcing the pairing.
* **`LLMTruncated` is its own type** because gpt-5-mini returns `content=''` with
  `finish_reason='length'` and *no error* when the budget is spent on hidden
  reasoning — that must never be mistaken for a refusal.
* **Retry with backoff** was added after 8 concurrent workers turned most
  extractor calls into 429s that the pipeline scored as abstentions — a quota
  artifact masquerading as the system declining to answer.

---

## 8. Evaluation (§20.3, §26)

| Module | Role |
|---|---|
| `eval/shapes.py` | answer-shape classifier — reproduces **52 / 35 / 26 / 23** exactly |
| `eval/gold.py` | gold-page mapping by evidence overlap; the `UNANSWERABLE` list |
| `eval/scorer.py` | the rubric, one predicate per shape |
| `eval/report.py` | per-shape reporting; dev/blind split **by company** |
| `eval/judge.py` | LLM judge for the two free-text shapes |
| `eval/negatives.py` | the not-found set — 5 categories, 60 items |
| `eval/ablate.py` | 12 variants that produce the approach-note table |

The phrase↔multi-sentence boundary is *"does the answer assert more than one
citable claim?"* — a list marker, two sentences, or two figures. That reproduces
the measured split exactly; a length threshold does not.

**The abstain baseline scores exactly 0.000** — the harness self-check, written
before any answering code (§19.5 rule 1).

### ~7 practice questions are unanswerable from the supplied corpus
This contradicts the recorded "0/136 are unanswerable". The J&J and PepsiCo 8-K
files are the **wrong filings** — inline-XBRL cover dates 2023-01-24 and
2023-02-09 do not match the events their filenames and questions refer to — and
omit the Exhibit 99.1 the gold evidence is quoted from. CVS's income-statement
figures appear nowhere in its HTML.

Excluded from the accuracy denominator and **reported, never dropped**. They are
also the only *natural negatives* the practice set contains.

---

## 9. Product surface

`api/main.py` + `api/schemas.py` (FastAPI) and `app_streamlit.py`. The four
graded controls: **Add filing** upload with a visible processing indicator, a
chat box, evidence on every answer, and a plain decline path using the exact
string `Not found in this filing.` An unhandled server error returns that same
refusal rather than a confident answer.

---

## 10. Governance — §25 and §26

Added to the plan this session and enforced mechanically, not promised.

`tests/test_generalisation_guard.py` — **107 checks** — fails the build if any
module under `ingest/ retrieval/ query/ storage/ api/ llm/` reads a benchmark
id, a gold field, or hardcodes a document identity; if anything but `config.py`
reads the environment; or if anything but `llm/` imports a vendor SDK.

The line drawn: router weights, the alias table, anchor regexes and the formula
book are **general mechanisms tuned on this corpus** — they would be written the
same way for 10,000 unseen filings. A lookup from question to document would
not be.

---

## 11. First end-to-end baseline, and the honest caveat

16 questions before the run stalled on rate-limit backoff:

| | |
|---|---|
| Total | **+1** over 16 (mean **+0.06**) |
| +1 / 0 / −1 | **2 / 13 / 1** |
| False-answer rate | **6.2%** |

**The system over-abstains, and the cause is measured**: verifier B (adversarial)
accounts for 5 of 11 abstentions, verifier A a further 2. This is exactly what
§22.7 predicted — "Default to REFUTED when uncertain" plus a `unanimous` policy
rejects correct answers, because refutation framing is a *substitute* for a
second model family, not an addition to it.

**The fix is a config change, and it must be chosen on the calibration curve
rather than by eye** — a configuration that raises the score while raising the
false-answer rate is a regression. `eval/ablate.py` carries the variants.

**Nondeterminism is real**: the same question scored +1 on one run and failed G1
on the next, because the extractor chose a different quote. Any single-run
number carries variance, and the ablation table must average over repeats.

---

## 12. Removed

The superseded modules were deleted with explicit approval and replaced per
§24.2: `ingestion/`, `service.py` (filing-local by construction), `abstention/`,
`evidence/`, `reasoning/`, `verification/`, `app/`, the retrieval modules
superseded by the new ones, `schema_sqlite.sql`, `schema_duckdb.sql`,
`evaluation/run_eval.py` (scored by FinanceBench `evidence_page_num`, which
indexes a third-party PDF we cannot reproduce for an uploaded filing), an
orphaned Bedrock client, and their two tests.

`pyproject.toml` still declared duckdb/boto3/arelle and `requires-python >=3.11`
against a 3.10 venv; realigned with `requirements.txt`.

---

## 13. What to do next

1. **Calibrate abstention** against the 60 synthetic negatives + 7 natural ones.
   Run the ablation variants and pick the operating point on the score /
   false-answer-rate curve, on the **blind** split. `verifier_b_adversarial:
   false` is the first thing to measure.
2. **Full 136-question baseline with repeats**, once the operating point is set.
3. **Dense embeddings (S10) and page summaries (S9)** — built, behind config
   flags, still unpopulated. Anchors + BM25 alone already reach 80.3%.
4. **XBRL fact path (S8)** — repairs the ~72% of data tables whose alignment
   fails.
5. **Raise table alignment above 28%** — Nike, AMD and J&J are the weak filings.
