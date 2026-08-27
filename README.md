# The Analyst Copilot

A question-answering chatbot over SEC annual/quarterly filings. Given a
filing and an analyst-style question, it returns a precise answer plus the
exact place in the document it came from — or an honest
**"Not found in this filing."**

Scoring rubric (see `The_Analyst_Copilot 1.pdf`):

| Result | Score |
|---|---|
| Correct answer, correct location | +1 |
| "Not found in this filing" | 0 |
| Correct answer, wrong location | 0 |
| Confidently wrong answer | −1 |

The architecture follows `Building a Trustworthy SEC Filing Analyst
Copilot.md` — read that first for the full rationale. Short version: parse
EDGAR HTML/Inline XBRL losslessly, retrieve typed evidence (lexical
first), calculate with code instead of the LLM, verify independently, then
answer or abstain.

## Status

This is an early-stage scaffold, not a finished system. What's real today:

- **Ingestion (Phase 1, functional):** loads a filing's HTML, content-hashes
  it, and parses it into a typed block tree (sections, paragraphs, tables,
  rows, cells) with a stable DOM-xpath locator on every node.
- **Storage (functional):** SQLite holds the typed tree plus an FTS5
  (BM25) index; DuckDB is wired up for the fact/cell ledgers.
- **Retrieval (partial):** filing-local BM25 search over narrative text and
  table headers works, verified end-to-end (correctly abstains when no
  hits, returns evidence when it finds a match). Known weakness: plain
  FTS5 `bm25()` favors short, high-density blocks (a single table-header
  cell literally containing "FY2023") over the longer paragraph that
  actually explains something, because it isn't structure-aware. This is
  exactly the gap the design doc's Phase 3 (routing + reranking + local
  expansion) exists to close — table_lookup should prefer whole rows with
  header context, narrative_lookup should prefer paragraphs. Dense/hybrid
  retrieval and reranking are not built yet.
- **Reasoning / calculation (partial):** the `AnswerPlan` schema and a
  Decimal-based calculator exist; the planner is a keyword router today,
  not an LLM-based decomposer, and nothing calls the calculator yet.
- **Verification (partial):** deterministic checks (evidence exists,
  belongs to the right filing, slots resolved) run before every answer.
  Model-based verification is not built.
- **Abstention (partial):** the exact refusal string and a deterministic
  gate are implemented. Threshold calibration against hard negatives is
  not done — see `evaluation/negatives/`.
- **XBRL fact extraction:** **not implemented.** Numeric answers currently
  come only from parsed HTML table cells, not tagged Inline XBRL facts.
  This is the highest-value next piece of work (`ingestion/xbrl_parser.py`).
- **UI:** a Streamlit app with an "Add filing" control (upload + visible
  processing status), a chat box, evidence shown on every answer, and the
  abstention path.

Every module's docstring says explicitly what's implemented vs. stubbed —
start there before assuming a function is production-ready.

## Setup

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate | macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
pip install -e .            # so `analyst_copilot` imports cleanly everywhere

cp .env.example .env        # then set ANTHROPIC_API_KEY (optional — the
                             # system runs without it, minus LLM answer
                             # synthesis and verification)
```

## Data

Place the contest data under `data/`:

```
data/filings/                    # the 78 filings from analyst-copilot-data.zip
data/practice-questions.jsonl
```

`practice-questions.jsonl` is FinanceBench format: each record has
`doc_name` (matches a filing's filename stem), `question`, `answer`, and an
`evidence` list with `evidence_page_num` (the filing's own printed page
footer number, 0-indexed — see `ingestion/html_parser.py`'s page-mapping
docstring for how that's reproduced from the raw HTML).

## Running

Initialize the databases:

```bash
python -m analyst_copilot.storage.db
```

Ingest filings (one file, or a whole directory):

```bash
python scripts/ingest_filing.py data/filings/example.htm --company "Acme Inc" --form-type 10-K
python scripts/ingest_filing.py data/filings/ --batch
```

Inspect what got extracted for a filing (Phase 1 sanity check):

```bash
python scripts/inspect_evidence.py <filing_id_prefix>
```

Launch the chatbot:

```bash
streamlit run src/analyst_copilot/app/streamlit_app.py
```

Run tests:

```bash
pytest
```

Score against the practice set (ingest the filings referenced by
`doc_name` first, or the script skips unmatched questions and tells you):

```bash
python evaluation/run_eval.py data/practice-questions.jsonl
```

## Project layout

```
src/analyst_copilot/
  ingestion/      raw HTML -> typed block tree (+ XBRL, pending)
  storage/        SQLite (tree + FTS5) and DuckDB (fact/cell ledgers) schemas
  retrieval/      router, BM25, table/fact lookup, local expansion
  reasoning/      AnswerPlan schema, deterministic calculator, planner
  verification/   deterministic checks + verifier orchestration
  evidence/       the evidence-first claim/candidate contract
  abstention/     the "Not found in this filing." policy
  llm/            single wrapped entry point for all model calls
  app/            Streamlit product UI
evaluation/       scoring harness against practice-questions.jsonl
scripts/          CLI ingestion + evidence inspection
tests/            parser and abstention-policy unit tests
```

## Roadmap

Follows the design doc's build order:

1. ~~Deterministic evidence locations~~ (this scaffold)
2. Typed table + XBRL fact layer (Arelle integration, unit/scale/sign
   normalization into `cell_ledger`/`fact_ledger`)
3. Hybrid retrieval (dense complement + reranking) and hard negatives
4. Bounded planner + calculator wiring (multi-hop evidence slots)
5. Model-based verifier + calibrated abstention threshold, evaluated on a
   held-out negative set

See `APPROACH_NOTE.md` for what's been tried, measured, kept, and thrown
away as this progresses.
