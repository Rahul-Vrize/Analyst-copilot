# BUILD LOG

Measured findings from building IMPLEMENTATION_PLAN §19. Everything here was
verified against the real corpus (W5). Each item either corrects or extends a
fact recorded in HANDOFF.md / PROJECT_CONTEXT.md.

---

## STATUS

| | |
|---|---|
| Schema | ✅ applied to Azure Postgres; 12 tables, `vector(1024)`, hnsw, idempotent |
| Corpus | ✅ **78/78 filings ingested, 0 failures, 216 s** |
| Tests | ✅ **210 passing**, 6 skipped |
| Router | ✅ top-1 **95.6%**, top-4 **98.5%** |
| Retrieval | ✅ reproduces every plan target (§5) |
| Pipeline | ✅ end-to-end; first baseline recorded (§8) |
| Product | ✅ FastAPI (6 endpoints) + Streamlit; all four graded controls |
| ⚠️ Calibration | **NOT DONE** — the system over-abstains; verifier B is the cause (§8) |

Corpus now holds: 8,389 pages · 372,081 blocks · 13,357 tables · 29,412 typed
cells · 2,050 sections · 93 company aliases.

Per-filing ingest is ~3 s, against a 10-minute budget for an upload.

---

## 1. Database migration — APPLIED

`migrations/001_init.sql` ran clean once all three extensions were allowlisted
in the Azure `azure.extensions` server parameter (**VECTOR, PG_TRGM, UNACCENT** —
the first attempt failed because only VECTOR was set, and the smoke test's
"available" check reads `pg_available_extensions`, which reports what the server
*could* install, not what it is *permitted* to).

Verified: 12 tables · `pages.embedding` is `vector(1024)` and matches
`EMBEDDING_DIMENSIONS` · hnsw + GIN(tsv) + GIN(trgm) indexes present ·
`tsv` generated column · **re-running the migration exits 0** (idempotent, which
is what a grader's from-zero run depends on).

---

## 2. Three measured corrections to the page splitter

### 2.1 An `<hr>` inside a `<table>` is a cell rule, not a page seam
`MICROSOFT_2016_10K` has **1,836 `<hr>`, 1,728 of them inside a `<td>`** —
underlines beneath figures. Treating them as seams split that filing into **343
pages instead of 107**, shredding every financial statement. It is the only
filing where `<hr>` outnumbers CSS breaks, and the `<table>`-ancestor test is a
general structural rule (a page seam cannot occur inside a table cell), not a
per-document patch.

### 2.2 Three filings are EDGAR *full submissions* — we were reading the envelope
`AMCOR_2022_8K…`, `FOOTLOCKER_2022_8K…05-20`, `FOOTLOCKER_2022_8K…08-19` wrap
several concatenated `<DOCUMENT>` sections, each a complete `<html>`. lxml parses
**only the first**, so we were extracting ~3.5k characters of EDGAR
submission-index chrome and **none of the filing**. AMCOR's 8-K body sits at byte
82,776 of 142,400 and never reached the index.

**This also explains a recorded "fact":** these same three filings were the only
ones reported as having *zero page-break markers*. They have none because we were
never looking at their content. Corrected: **all 78 filings have markers.**

Exhibits are kept (gold evidence for several 8-K questions is in an EX-99.1 news
release); XBRL/XML viewer scaffolding is dropped so it cannot flood the lexical
index. New module `ingest/edgar.py`. Effect: AMCOR 1→7 pages, Foot Locker 1→4
and 1→31.

### 2.3 Result

| Metric | Plan | Measured |
|---|---|---|
| Total derived pages | ~8,600 | **8,389** |
| Filings with page-break markers | 75/78 | **78/78** |
| Gold pages mapped (Jaccard ≥ 0.35) | 126/136 | **127/136** |

The page-count drop is almost entirely Microsoft's 343 → 107 correction.

---

## 3. ⚠️ ~7 practice questions are NOT answerable from the supplied corpus

This **contradicts the recorded "0/136 questions are unanswerable"** and changes
what a good score looks like. Encoded in `eval/gold.py::UNANSWERABLE`, excluded
from the accuracy denominator, and **reported, never silently dropped**.

`JOHNSON_JOHNSON_2023_8K_dated-2023-08-30.htm` contains only the 8-K cover page,
Item 2.02 and signatures. It **references Exhibit 99.1 but does not include it**,
and the gold evidence for all three J&J questions is text from that exhibit.
Worse, the file's own inline-XBRL cover date is **2023-01-24**, not the
2023-08-30 its filename claims — **the supplied file is a different 8-K** from
the one the questions ask about (they ask about the August Kenvue separation).
`PEPSICO_2023_8K_dated-2023-05-05` has the same shape (cover date 2023-02-09; the
question asks about the May 3 AGM). CVS's income-statement figures
(`183,910`, `194,579`) appear nowhere in its HTML in any formatting.

| Questions | Cause | Correct behaviour |
|---|---|---|
| `01488`, `01490`, `01491` | J&J 8-K: wrong filing supplied, exhibit absent | **abstain → 0** |
| `01482`, `00705`, `00882` | PepsiCo 8-K: wrong filing supplied, exhibit absent | **abstain → 0** |
| `05915` | CVS 10-K: figures absent from the HTML | **abstain → 0** |

**Consequences.** The practical ceiling on the practice set is ~**129/136**. And
these are **natural negatives** — real questions with genuinely absent evidence,
a higher-fidelity abstention signal than anything synthetic. They supplement,
but cannot replace, the synthetic negative set required by §26 (too few to
calibrate a threshold).

Two further questions (`00394` JPMorgan, `00724` Pfizer) sit just under the
containment threshold purely because the *gold* text has words run together
(`Segment AssetsWe manage…`) — a PDF-extraction artifact on the gold side. The
content is present and correctly located.

---

## 4. Table pipeline — the alignment gate

Counts match the plan closely: **1,403 tables** across the six probe filings
(plan measured 1,397), JPMorgan **640** (as recorded), **48.9%** survive the
layout filter (plan: 46% are artifacts).

Two symmetric header-extraction bugs were found and fixed, 30 tables each way on
`MICROSOFT_2023_10K` alone:

* `hdr > val` — `"Year Ended June 30,"` is a descriptor spanning the whole
  header, not a column label. It gave 4 tokens against 3 value columns.
* `hdr < val` — `"Percentage Change"` **is** a real column but carries no period
  token, so it was dropped. That gave 2 tokens against 3 columns.

Alignment rose **21.9% → 31.2%**; Microsoft **22.1% → 48.1%**, 3M **46.8%**.
Nike / AMD / J&J remain the weak ones — exactly the state §16.3 records. The
plan's 40–60% refers to *statement* tables; this figure is over all data tables.
Corpus-wide: **28.0%** of data tables align, yielding 29,412 typed cells.

Also fixed: `(Millions)` alone was not recognised as a units note (only
`(In millions, …)` was), leaving `scale` NULL and making gate G5 abstain for a
fixable reason.

**The gate behaves as designed** — where alignment fails there is markdown and
**no typed cells**. Fail closed: a typed fact exists only when its provenance is
provable.

**Verified end-to-end on the canonical example:** gold `financebench_id_03029`
(3M FY2018 capex, `$1577.00`) maps to derived page **61** at Jaccard 0.991, and
the typed cell on page 61 reads
`Purchases of property, plant and equipment (PP&E) | 2018 | -1577 | millions` —
row label matching the gold justification verbatim.

---

## 5. Router and retrieval reproduce the plan's targets

### Router (`query/router.py`, no LLM)

| | Plan | Measured |
|---|---|---|
| top-1 | 94.1% | **95.6%** (130/136) |
| top-2 | 97.1% | **97.8%** |
| top-4 | 98.5% | **98.5%** (134/136) |

The 2 top-4 misses are exactly the documented questions naming **no company**;
both take the clarify path (0), never a guess (−1).

### Retrieval (`scripts/measure_retrieval.py`, from the live database)

| Configuration | Plan | Measured | Pages |
|---|---|---|---|
| anchors only (oracle doc) | 73.0% | **73.2%** | 25.2 |
| BM25@20 only (oracle doc) | 18.3% raw | **46.5%** | 19.8 |
| anchors + BM25@20 (oracle doc) | 85.7% | **85.0%** | 39.1 |
| anchors + BM25@20 (router top-4) | 81.0% | **80.3%** | 77.8 |
| escalated BM25@40 (router top-4) | 84.1% | **86.6%** | 117.0 |

BM25 alone far exceeds the plan's 18.3% raw-page baseline because it indexes the
**composite `lexical_text`** (context header + verbatim table/section headers),
which is exactly the lift §16.2 predicted.

**Two fixes were needed to reach these numbers, both measured:**

1. **Statement hints must RANK, not FILTER.** Filtering anchors to the hinted
   statements gave 68.5% from 15.5 pages; keeping every statement page and
   merely ordering by hint gives **73.2% from 26.6 pages** — the plan's recorded
   "73.0%, ~27 pages". Anchors are a recall stage; verification is the precision
   gate, so discarding a statement is a pure loss.
2. **BM25 top-k must be allocated PER CANDIDATE FILING.** A single global top-20
   over the router's 4 candidates lets a strongly scoring wrong filing crowd out
   the gold filing: **74.8% → 80.3%** when k is allocated per document.

Candidate-pool size at tier 1 (~78 pages) exceeds `assembly_token_budget`
(42k). That is correct at this stage: recall lost in retrieval cannot be
recovered, and the assembly stage (RRF → rerank → budget) is what trims it.

---

## 6. Built this session

| Component | Plan ref |
|---|---|
| `config.py` — typed Settings, the ONLY env reader | §24.1, §24.4 |
| `ids.py` — derived identifiers, one definition | — |
| `ingest/pages.py` · `edgar.py` · `catalog.py` · `blocks.py` · `tables.py` · `sections.py` · `pipeline.py` | §21.1, §16.3, §14.2 |
| `storage/db.py` · `repository.py` — SQLite/DuckDB → PostgreSQL | D7, §24.2 |
| `query/router.py` — deterministic document router | §22.2 |
| `retrieval/base.py` · `bm25.py` · `anchors.py` · `fusion.py` | §22.3, D10 |
| `llm/base.py` · `azure_openai.py` — the provider seam | §22.7 |
| `eval/shapes.py` · `gold.py` · `scorer.py` · `report.py` · `run_eval.py` | §20.3 |
| `scripts/ingest_all.py` · `measure_retrieval.py` | §19.2 |
| `tests/` — pages, tables, router, generalisation guard | §24.5 |

**Answer-shape classification reproduces the plan exactly: 52 numeric / 35
yes-no / 26 phrase / 23 multi-sentence.** The phrase↔multi-sentence boundary is
"does the answer assert more than one citable claim?" — a list marker, two
sentences, or two figures. A length threshold does not reproduce the split; this
does, and it is the property the location predicate actually depends on.

**The abstain baseline scores exactly 0.000 with a 0% false-answer rate**, which
is the harness self-check: written before any answering code (§19.5 rule 1).

### Generalisation guard (§25)
`tests/test_generalisation_guard.py` enforces the new mandate mechanically —
**83 checks**: no benchmark id, practice-question file, gold field, or hardcoded
document identity in `ingest/ retrieval/ query/ storage/ api/ llm/`; only
`config.py` reads the environment; only `llm/` imports a vendor SDK.

---

## 7. Repo changes that were required

* **`config.yaml`** declared `models.embeddings.dimensions: 1536` while `.env`
  said `1024` and the schema is `vector(1024)`. That mismatch fails **every**
  page insert at the end of a long ingest. Corrected to 1024 and now asserted at
  startup in `config.py`.
* **`pyproject.toml`** still declared the superseded stack (duckdb, boto3,
  arelle) and `requires-python >=3.11` against a 3.10 venv. Realigned with
  `requirements.txt`.

### ⚠️ Left alone — needs your decision (git-tracked, teammate's)

* `tests/test_html_parser.py` — **3 pre-existing failures**, not caused by this
  work: `FilingRecord` requires `doc_name` but the test's own fixture omits it.
  Both files are the teammate's and untouched here. That module also tests the
  superseded `<h*>`-based parser (only 6/78 filings contain any `<h*>` tag).
* `src/analyst_copilot/ingestion/` (old) is superseded by `src/analyst_copilot/ingest/`
  (new). `evaluation/run_eval.py` is superseded by `src/analyst_copilot/eval/` —
  the old one scores by FinanceBench `evidence_page_num`, which indexes a
  third-party PDF we do not have and cannot reproduce for an uploaded filing.

**No git commands were run** (W1). Nothing is staged or committed.

---

## 8. END-TO-END PIPELINE — first measured baseline

The full spine now runs: **route → retrieve → extract → compute → verify →
answer**, against the live database and the live model deployment.

### The number (§19.5 rule 4 — record it before changing anything)

16 questions scored before the run stalled on rate-limit backoff:

| | |
|---|---|
| Total | **+1** over 16 (mean **+0.06**) |
| +1 / 0 / −1 | **2 / 13 / 1** |
| False-answer rate | **6.2%** (target < 5%) |
| By shape | numeric **+2** (n=8) · yes-no 0 (n=5) · phrase 0 (n=1) · multi-sentence **−1** (n=2) |

**This is an over-abstaining system**, and the cause is measured rather than
guessed:

| Abstention cause | n |
|---|---|
| **verifier B (adversarial)** | **5** |
| G4 (missing operands) | 3 |
| composer declined | 2 |
| verifier A | 1 |
| both verifiers | 1 |
| G1 (quote not on page) | 1 |

**Verifier B alone accounts for the largest share.** This is exactly what
IMPLEMENTATION_PLAN §22.7 predicted: with `same_model_adversarial`, "Default to
REFUTED when uncertain" plus a `unanimous` policy rejects correct answers.
Refutation framing is a *substitute* for a second model family, not an addition
to it.

**The fix is a config change, not code** — and it must be chosen on the
calibration curve rather than by taste:

```yaml
verification:
  verifier_b_adversarial: false     # or verifier_policy: any
```

`eval/ablate.py` carries the variants that measure it (`no_llm_verifiers`,
`verifier_b_not_adversarial`, `no_gates`, `no_G1`, …). **Do not tune this by
eye**: a configuration that raises the score while raising the false-answer rate
is a regression, because a −1 costs two points against a refusal.

### Four defects found by running it, each fixed

1. **The extractor had to invent its own citation.** The context header read
   `[3M | 10-K | FY2018 | … | p.47]` and never contained the literal `doc_id`,
   so the model guessed — and cited page 62 for a quote on page 61. G1 caught
   it and the system abstained on a question it had right. Fixed with an
   explicit machine-readable locator (`<<< doc_id=… page_seq=… >>>`).
2. **Label-only quotes.** The extractor quoted *"Purchases of property, plant
   and equipment (PP&E)"* with no figures. Every gate passed and both verifiers
   then rejected it — correctly. Added **G1b**: the reported figure must appear
   inside its own quote. Deterministic, free, and catches it a tier earlier.
3. **G1b keyed quotes by page**, so when two slots cited the same page (normal —
   a statement supplies several line items) operand A's digits were checked
   against operand B's quote. Fixed to use each operand's own quote.
4. **Rate limits were being scored as abstentions.** At 8 workers most extractor
   calls failed with 429 and the pipeline recorded `extractor_error`, which is a
   deployment-quota artifact masquerading as the system declining to answer.
   Added retry with exponential backoff in the one place §22.7 designates, and
   the real error message is now kept in the trace.

### The missing stage: ANSWER

The largest single gap was architectural, not a bug. `_render_answer` returned
the first numeric slot — but ✅ **84/136 gold answers (62%) are non-numeric**.

Measured: asked *"which segment has dragged down 3M's overall growth in 2022?"*
(gold: *"The consumer segment shrunk by 0.9% organically"*) the pipeline
answered **`8,902`**. Every deterministic gate passed, because the figure was
correctly quoted and correctly located. It was simply not an answer to the
question — **a relevance failure the gates structurally cannot catch**.

`query/compose.py` now implements the spine's `ANSWER` stage, switching on
answer shape. Even a scalar goes through it: the bare `1,577` was rejected by
verifier A for stating neither unit nor period, while
`"$1,577 million in FY2018 for purchases of property, plant and equipment
(PP&E)"` passes all seven gates and both verifiers, and scores **+1**.

### Nondeterminism is real and must be reported

The same question (`financebench_id_03029`) scored **+1** on one run and failed
G1 on the next, because the extractor chose a slightly different quote. Any
single-run score therefore carries run-to-run variance, and the ablation table
must average over repeats rather than compare single runs.

---

## 9. Built since the last entry

| Component | Plan ref |
|---|---|
| `llm/base.py` · `azure_openai.py` · `anthropic_foundry.py` · `cohere_foundry.py` · `registry.py` · `schemas.py` · 10 versioned prompts | §22.4, §22.7 |
| `query/compute.py` — AST-whitelisted `Decimal` calculator | D5, §20.5 |
| `query/formula_book.py` + `formula_book.yaml` — 25 metrics, 23 operands | §20.5, §17.3 |
| `query/gates.py` — G1, **G1b**, G2–G7 | §20.2 |
| `query/extract.py` · `compose.py` · `pipeline.py` (the spine) | §18.1, §4 |
| `retrieval/assemble.py` · `rerank.py` | §4, §20.1 |
| `container.py` — composition root | §24.2 |
| `api/schemas.py` · `api/main.py` — FastAPI, 6 endpoints | §8, §22.5 |
| `app_streamlit.py` — all four graded product controls | D3 |
| `eval/judge.py` · `negatives.py` · `ablate.py` | §20.3, §26 |
| `scripts/` — `run_pipeline_eval` · `run_negatives_eval` · `make_negatives` · `measure_retrieval` | §19 |
| `README.md`, `APPROACH_NOTE.md` | §19.4 |

**210 tests passing.** The superseded teammate modules (`ingestion/`,
`service.py`, `abstention/`, `evidence/`, `reasoning/`, `verification/`, the
SQLite/DuckDB schemas, the old `evaluation/run_eval.py`) were removed with your
approval and replaced per §24.2.

**⚠️ A prompt and its JSON schema must declare the same fields.** Measured: the
router prompt asked for `form_types` and `periods` while the schema omitted
them, and gpt-5-mini — which follows instructions with "surgical precision" —
satisfied both by cramming the missing fields into a string field
(`metric_name: "revenue','form_types':[],"`). That is §23.3 in miniature. The
schemas now live beside the prompt loader and a test enforces the pairing.

---

## 10. Next

1. **Calibrate abstention** on the 60-item negative set + the 7 natural
   negatives. Run `eval/ablate.py` variants and choose the operating point on
   the score / false-answer-rate curve, on the **blind** split (§25.3 rule 4).
   `verifier_b_adversarial: false` is the first thing to measure.
2. **Full 136-question baseline** with repeats, once the operating point is
   chosen. Budget ~2 s/question of API time at 4 workers, plus backoff.
3. Dense embeddings (S10) and page summaries (S9) — both behind config flags,
   both still unpopulated. Anchors + BM25 alone already measured 80.3%.
4. XBRL fact path (S8) — repairs the 72% of data tables whose alignment fails.
5. Raise table alignment above 28%; Nike/AMD/J&J are the weak filings.
