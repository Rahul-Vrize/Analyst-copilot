# Approach Note — The Analyst Copilot

*"We read this as carefully as we read your score."*

---

## 1. What the score is actually asking for

| Outcome | Score |
|---|---|
| Correct answer, correct location | **+1** |
| `Not found in this filing.` | **0** |
| Correct answer, **wrong location** | **0** |
| Confidently wrong answer | **−1** |

A wrong answer costs **two points relative to a refusal**. So the task is
**precision-calibrated selective answering**, not accuracy maximisation — and a
component that raises the answer rate while raising the false-answer rate is a
regression, not an improvement. Every measurement below reports both.

The second constraint shapes everything else: **the chatbot holds all 78 filings
and the user never selects one.** This is the shared-corpus setting, where
FinanceBench's own shared-vector-store baseline scored **~19%**. Document
routing is therefore a first-class stage, and a routing error is a −1, not a 0.

---

## 2. The finding the design rests on

We measured retrieval on the real corpus before building anything:

| Approach | Gold page in the candidate set |
|---|---|
| Corpus-wide BM25 | **5.6%** |
| BM25 with the correct document handed over (oracle) | **18.3%** |
| **Structure anchors alone** — 7 regexes over page headers, no LLM | **73.2%** |
| Anchors + BM25@20, oracle document | **85.0%** |
| Anchors + BM25@20, real router at top-4 | **80.3%** |

**Retrieval in SEC filings is navigation, not similarity search.** Structure
beats lexical search by more than 3×, because ~75% of gold evidence sits in the
three primary financial statements and those have stable, recognisable titles.

That single result determined the architecture: find the *document*, then the
*statement*, then the *page* — and use similarity search only to fill gaps.

---

## 3. The pipeline

```
route → navigate → RETRIEVE → extract → COMPUTE → verify → ANSWER
```

One spine, switchable stages. **This is a deterministic workflow, not an
agent.** Control flow lives in code; the model chooses *content* — which
evidence, which formula, which wording — never *what happens next*. In loose
industry terms it is "agentic RAG"; the honest label is **structure-aware RAG
with an evidence-first verification gate**, and we would rather say that than
claim autonomy we deliberately avoided.

**1 · Route.** A deterministic scorer — no LLM — ranks all 78 filings on company
alias, fiscal year, form type and 8-K event date.

| | Measured |
|---|---|
| top-1 | **95.6%** |
| top-4 | **98.5%** (134/136) |

The two misses name no company at all; both take a **clarifying question**,
because a wrong document is −1 and a clarification is free. Escalation deepens
*inside* the top-4 rather than widening: top-4 and top-8 both reach 134/136, so
more documents buy nothing.

**2 · Retrieve.** Structure anchors ∪ in-memory BM25 over a composite lexical
field, with **k allocated per candidate filing** — a single global top-20 lets a
strongly-scoring wrong filing crowd out the right one (74.8% → 80.3%).

**3 · Extract.** Evidence *slots*, never prose: each carries a value, unit,
period, location and a **verbatim quote**. A quote can be checked
character-by-character; a prose answer cannot.

**4 · Compute.** Python `Decimal` over an AST-whitelisted expression. The model
never does arithmetic — program-of-thought execution is measured elsewhere to
remove 88% of arithmetic errors. Formula precedence: a definition supplied *in
the question* (14/136 questions carry one) → a curated 25-metric book → an
LLM-proposed formula behind four checks → refuse.

**5 · Verify.** Deterministic gates first, then two isolated LLM verifiers.

---

## 4. The gates — where the −1s are prevented

| Gate | Predicate |
|---|---|
| **G1** | the quote appears **verbatim** on the cited page |
| **G1b** | the reported figure appears **inside its own quote** |
| G2 | every cited document is in the routed candidate set |
| G3 | the evidence period is within the filing's `coverage_years` (or intent is forecast) |
| G4 | no missing operands (for answers built from operands) |
| G5 | operand units and scales are compatible |
| G6 | re-evaluating the formula reproduces the stated answer |
| G7 | the cited page exists |

**G1 is the single most important piece of code in the system.** It makes an
invented figure or a fabricated citation *structurally impossible*, and it costs
one string search. The gates are deterministic and model-independent, which is
what carries the system given the constraint in §6.

They are a list of objects, not an if-chain, so the ablation runner can disable
exactly one and measure what it was worth.

---

## 5. What measurement changed — three corrections

We reversed three plausible beliefs by testing them.

**1 · `<hr>` is not a page break.** `MICROSOFT_2016_10K` contains 1,836 `<hr>`,
of which **1,728 sit inside a `<td>`** — they are cell rules under financial
figures. Treating them as page seams split that filing into **343 pages instead
of 107**, shredding every statement. A page seam cannot occur inside a table
cell; that structural rule fixed it without touching any other filing.

**2 · Three filings are EDGAR full submissions.** They wrap several concatenated
`<html>` documents, and `lxml` parses only the first — so we were extracting
~3.5k characters of EDGAR navigation chrome and **none of the filing**. AMCOR's
8-K body sits at byte 82,776 of 142,400. This also explained a "fact" we had
recorded: those three were the only filings with *no page-break markers*. They
have none because we were never looking at their content. Corrected: **all 78
have markers**, and gold-page mapping rose to **127/136**.

**3 · The benchmark contains defects.** ✅ **7 of the 136 practice questions
cannot be answered from the supplied corpus.** The J&J and PepsiCo 8-K files are
the *wrong filings* — their inline-XBRL cover dates (2023-01-24, 2023-02-09) do
not match the events their filenames and questions refer to — and they omit the
Exhibit 99.1 the gold evidence is quoted from. CVS's income-statement figures
appear nowhere in its HTML in any formatting.

We report these separately rather than fitting to them, because fitting would
mean fabricating evidence. They are also the only **natural negatives** the
practice set contains, and a real question with genuinely absent evidence is a
better abstention signal than anything synthetic.

---

## 6. What we did not get, and how we handled it

**Verifier independence is degraded, and we would rather say so.** The design
called for two model *families* at the generate→verify seam, because a model
checking its own output is not verification. Only `gpt-5-mini` is deployed to
us, so both verifiers are the same model and independence comes from
**adversarial framing plus context isolation**, not architecture. Verifier B is
told to *refute*, and sees only question + answer + quotes — never verifier A's
verdict, never the extractor's reasoning.

This is survivable because **the deterministic gates do most of the work and are
family-independent**. The LLM verifiers are the last net, not the first.

The swap path is built, not deferred: moving verifier B to another family is
three lines in `.env` and one config value, with an adapter already written and
imported in tests. When that happens, adversarial framing must be turned **off**
— it is a substitute for independence, not an addition — and the abstention
threshold re-calibrated.

---

## 7. Generalisation

**The 136 questions are test data, not the specification.** Judges may ask
different questions over filings we have never seen, so every stage is generic
and 100% accuracy is explicitly not the target.

This is enforced mechanically, not promised: `tests/test_generalisation_guard.py`
fails the build if any module under `ingest/ retrieval/ query/ storage/ api/
llm/` reads a benchmark id, a gold field, or hardcodes a document identity —
and separately if anything but `config.py` reads the environment, or anything
but `llm/` imports a vendor SDK. Benchmark knowledge is confined to `eval/`.

The line we drew: **router weights, the alias table, anchor regexes and the
formula book are general mechanisms tuned on this corpus** — they would be
written the same way for 10,000 unseen filings. A lookup from question to
document would not be. The dev/blind split is **by company**, never by question,
so the blind number estimates unseen-company performance.

---

## 8. Honest limitations

* **Table alignment validates on 28% of data tables** corpus-wide. Where it
  fails there is markdown and **no typed cells** — fail closed: a typed fact
  exists only when its provenance is provable. Raising this is a measurable
  tuning task, not a redesign.
* **Latency is 60–130 s per question**, dominated by 4–6 sequential model calls
  on a rate-limited deployment. Batching and caching are unexploited.
* **Dense embeddings and page summaries are built but not populated.** The
  measured recall above is anchors + BM25 only; the composite lexical field
  already carries verbatim table and section headers, which is where most of the
  lift came from.
* **The abstention threshold is not yet calibrated** against the 60-item
  negative set. Until it is, we report the operating point we have rather than
  claiming a tuned one.
* Answers are currently composed one question at a time with no conversational
  memory.
