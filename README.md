# The Analyst Copilot

Question answering over SEC filings that returns a precise answer **plus the
exact document and page it came from**, or an honest **"Not found in this
filing."**

The chatbot holds every filing and the user never selects one. It works out
*which* document holds the answer and *where* inside it — the shared-corpus
setting, in which FinanceBench's own shared-vector-store baseline scored ~19%.

---

## Why it is built this way

The scoring rubric is asymmetric:

| Outcome | Score |
|---|---|
| Correct answer, correct location | **+1** |
| `Not found in this filing.` | **0** |
| Correct answer, **wrong location** | **0** |
| Confidently wrong answer | **−1** |

So the objective is **precision-calibrated selective answering, not accuracy**.
Two abstentions beat one wrong answer, and a marginal answer must clear
verification rather than merely look plausible.

The design follows from measurement, not intuition. The headline finding:
**retrieval here is navigation, not similarity search.** Structure beats
lexical search by 3×.

| Measured on the real corpus | |
|---|---|
| Corpus-wide BM25, gold page in top-10 | 5.6% |
| Oracle-document BM25, top-10 | 18.3% |
| Structure anchors alone (7 regexes, no LLM) | **73.2%** |
| Anchors + BM25@20, oracle document | **85.0%** |
| Anchors + BM25@20, real router at top-4 | **80.3%** |
| Deterministic document router | **top-1 95.6% / top-4 98.5%** |

---

## Quick start

### 1. Requirements
Python 3.10+, PostgreSQL 16+ with **pgvector**, and an Azure AI Foundry
deployment (or any OpenAI-compatible endpoint).

### 2. Database

**Local (the reproducible path):**
```bash
docker compose up -d          # pgvector/pgvector:pg17 on :5432
```

**Azure Postgres Flexible Server:** the three extensions must first be
allow-listed under *Server parameters → `azure.extensions`*: **VECTOR**,
**PG_TRGM**, **UNACCENT**. Without this, `CREATE EXTENSION` fails even though
the extensions are "available".

### 3. Install and configure
```bash
python -m venv .venv
.venv\Scripts\activate            # Windows;  source .venv/bin/activate on POSIX
pip install -r requirements.txt
pip install -e .

cp .env.example .env              # then fill in the values below
```

`.env` holds **secrets, endpoints and deployment names only**. Everything
tunable lives in `config.yaml`.

| Variable | Meaning |
|---|---|
| `DATABASE_URL` | percent-encode `@ : / #` in the password, or parsing breaks |
| `AZURE_OPENAI_ENDPOINT` | a `/openai/v1` URL uses `OpenAI(base_url=…)`; a classic one uses `AzureOpenAI(azure_endpoint=…)`. The client shape is detected from the URL |
| `AZURE_OPENAI_API_KEY`, `GPT_DEPLOYMENT` | chat/extraction/verification |
| `EMBEDDING_DEPLOYMENT`, `EMBEDDING_DIMENSIONS` | **must equal** `pages.embedding vector(N)` |
| `COHERE_RERANK_ENDPOINT`, `COHERE_RERANK_API_KEY` | reranker (optional) |
| `FILINGS_DIR`, `PRACTICE_QUESTIONS` | corpus locations |

### 4. Build the corpus
```bash
psql "$DATABASE_URL" -f migrations/001_init.sql     # idempotent, builds from zero
python scripts/ingest_all.py                        # 78 filings in ~3.5 min
```

### 5. Run
```bash
uvicorn analyst_copilot.api.main:app --port 8000    # API + /docs
streamlit run app_streamlit.py                      # UI on :8501
```

---

## How a question is answered

```
route → navigate → RETRIEVE → extract → COMPUTE → verify → ANSWER
```

One spine, switchable stages. **Control flow lives in code**: this is a
deterministic workflow, not an agent. The model chooses *content* — which
evidence, which formula — never *what happens next*.

1. **Route** — a deterministic scorer (no LLM) ranks all 78 filings by company
   alias, fiscal year, form type and 8-K event date. Top-4 candidates. If no
   company is named it asks a clarifying question rather than guessing, because
   a wrong document is −1 and a clarification is free.
2. **Retrieve** — structure anchors (financial-statement titles) ∪ in-memory
   BM25 over a composite lexical field, allocated **per candidate filing**.
3. **Assemble** — neighbour expansion, RRF, optional Cohere rerank, then trim
   to the token budget. The extractor only ever sees verbatim `raw_text`.
4. **Extract** — evidence *slots*, never prose: each carries a value, unit,
   period, location and a **verbatim quote**.
5. **Compute** — Python `Decimal` over an AST-whitelisted expression. The model
   never does arithmetic.
6. **Verify** — deterministic gates first, then two isolated LLM verifiers.

### The gates

| Gate | Predicate |
|---|---|
| **G1** | the quote appears **verbatim** on the cited page |
| **G1b** | the reported figure appears **inside its own quote** |
| G2 | every cited document is in the routed candidate set |
| G3 | the evidence period is within the filing's `coverage_years` (or intent is forecast) |
| G4 | no missing slots |
| G5 | operand units and scales are compatible |
| G6 | re-evaluating the formula reproduces the stated answer |
| G7 | the cited page exists |

**G1 is the single most important piece of code here.** It makes an invented
figure or a fabricated citation structurally impossible, which is exactly the
−1 case — and it costs one string search. The gates are deterministic and
model-independent, so they carry the system regardless of which model is behind
the verifiers.

---

## Testing

```bash
pytest                       # ~190 tests, no network required
python scripts/measure_retrieval.py     # reproduces the recall table above
python scripts/run_pipeline_eval.py --limit 24   # end-to-end rubric score
python scripts/make_negatives.py        # the not-found evaluation set
```

The measured numbers are **assertions**, not documentation: if a refactor drops
router top-4 below 98.5%, `tests/test_router.py` fails.

`tests/test_generalisation_guard.py` enforces the project's governing
constraint mechanically — no benchmark id, gold field, or hardcoded document
identity may appear in `ingest/ retrieval/ query/ storage/ api/ llm/`; only
`config.py` reads the environment; only `llm/` imports a vendor SDK.

---

## Layout

```
src/analyst_copilot/
├── config.py        typed Settings — THE ONLY env reader
├── container.py     composition root
├── ingest/          pages · edgar · blocks · tables · sections · catalog
├── retrieval/       anchors · bm25 · fusion · rerank · assemble
├── query/           router · extract · compute · formula_book · gates · pipeline
├── storage/         PostgreSQL + pgvector — the only SQL
├── llm/             the ONLY vendor-SDK importers; prompts/ are versioned files
├── api/             FastAPI
└── eval/            rubric scorer — the only package that may read the benchmark
migrations/001_init.sql
```

---

## Notes and limits

* **The 136 practice questions are test data, not the specification.** Nothing
  is tuned to them; judges may ask different questions over unseen filings.
  100% accuracy is explicitly not the target.
* **~7 practice questions are unanswerable from the supplied corpus** — the J&J
  and PepsiCo 8-K files are the *wrong filings* (their XBRL cover dates do not
  match their filenames) and omit the Exhibit 99.1 the gold evidence comes
  from; CVS's income-statement figures appear nowhere in its HTML. These are
  excluded from the accuracy denominator and reported separately.
* **Verifier independence is currently degraded.** Only `gpt-5-mini` is
  deployed, so both verifiers are the same model and independence comes from
  adversarial framing plus context isolation, not architecture. The swap path
  is built, not deferred: moving verifier B to another family is three lines in
  `.env`. See `BUILD_LOG.md`.
* Uploaded filenames must follow `COMPANY_YEAR_FORM.htm` — the name carries the
  catalog metadata the router filters on.
