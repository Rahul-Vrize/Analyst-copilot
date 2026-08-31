"""The document router - the organ the corpus-wide constraint demands.

The chatbot holds every filing and the user never picks one, so the system must
work out WHICH document holds the answer. This is the shared-corpus setting in
which FinanceBench's own shared-vector-store baseline scored ~19%, and a routing
error costs -1, not 0.

⚠️ THE WEIGHTS IN `score()` ARE MEASURED, NOT DESIGNED (IMPLEMENTATION_PLAN
§22.2). This exact scoring function produced top-1 94.1% / top-2 97.1% /
top-4 98.5% on the 136 practice questions. Re-deriving them will not reproduce
those numbers. Change them only against the eval harness.

No LLM is involved. That is the point: the highest-accuracy component in the
system is also the cheapest, and it is deterministic enough to assert in a
regression test (§24.5).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..ingest.catalog import FilingMeta

# ⚠️ TRAP, ALREADY PAID FOR ONCE: `\b20\d{2}\b` does NOT match "FY2022".
# There is no word boundary between "Y" and "2", so the year never matched and
# an early measurement reported top-1 at 48.5%. Use an explicit digit guard.
YEAR = re.compile(r"(?<!\d)(20\d{2})(?!\d)")
_FY2 = re.compile(r"fy\s?(\d{2})(?!\d)")

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
}
_MONTH_DAY = re.compile(
    r"\b(" + "|".join(MONTHS) + r")\s+(\d{1,2})", re.I
)

_QUARTER = re.compile(r"\bq([1-4])\b")
_8K_EVENT = re.compile(
    r"8-k|announce|press release|agm|vote|nominee|credit agreement|separation|spin",
)

# Eliminated: no alias for this company appears in the question.
ELIMINATED = -100.0


@dataclass(frozen=True)
class RouteResult:
    candidates: list[FilingMeta]
    scores: dict[str, float]
    company_named: bool
    years: set[int]
    needs_clarification: bool
    reason: str = ""


def normalise(question: str) -> str:
    """Lowercase and pad so alias matching can use whole-word containment."""
    return " " + re.sub(r"[^a-z0-9&\- ]", " ", question.lower()) + " "


def years_in(question: str) -> set[int]:
    """Every fiscal year named, in either `2022` or `FY22` form."""
    ys = {int(y) for y in YEAR.findall(question)}
    ys |= {2000 + int(y) for y in _FY2.findall(question.lower())}
    return ys


def score(
    question: str,
    filing: FilingMeta,
    aliases: dict[str, list[str]],
    max_year: dict[str, int],
    *,
    prefer_coverage_years: bool = True,
) -> float:
    """Score one filing against one question. Higher is better.

    Every constant here is measured - see the module docstring.
    """
    ql = normalise(question)

    # 1. HARD company gate. A filing whose company is not named is eliminated
    #    outright rather than down-weighted: answering from the wrong company's
    #    filing is the worst failure the rubric prices.
    company_aliases = aliases.get(filing.company_slug, [])
    if not any(f" {a} " in ql or f" {a}'s " in ql for a in company_aliases):
        return ELIMINATED
    s = 10.0

    # 2. Year. Rule A: when several years are named the LATEST wins, because a
    #    10-K carries comparatives - "CAGR from FY2020 to FY2022" is answered
    #    from the FY2022 filing. Measured worth: +7.3 points on its own.
    ys = years_in(question)
    if ys:
        if filing.fiscal_year == max(ys):
            s += 25
        elif filing.fiscal_year in ys:
            s += 8
        elif prefer_coverage_years and any(y in filing.coverage_years for y in ys):
            # The comparative columns genuinely contain the asked-for year, so
            # this is a lookup rather than a guess - but it must stay below an
            # exact fiscal-year match, which is always the better source.
            s += 4
        else:
            s -= 15
    else:
        # Rule B: no year named => prefer the company's newest filing.
        s += 6 if filing.fiscal_year == max_year.get(filing.company_slug) else -3

    # 3. Form and quarter.
    qq = _QUARTER.search(ql)
    is_q = bool(qq) or "quarter" in ql
    if filing.form_type == "10-Q":
        s += 6 if is_q else -6
        if qq and filing.fiscal_quarter == int(qq.group(1)):
            s += 6
        elif qq:
            s -= 4
    elif filing.form_type == "10-K":
        s += 4 if not is_q else -3
    elif filing.form_type == "8-K":
        s += 5 if _8K_EVENT.search(ql) else -4

    # 4. 8-K event-date proximity. Two same-company 8-Ks in one year are only
    #    separable by date (PepsiCo May-05 vs May-30; Foot Locker May-20 vs
    #    Aug-19), and the question names the event date.
    if filing.filing_date:
        for mon, day in _MONTH_DAY.findall(question.lower()):
            ev = (MONTHS[mon], int(day))
            fd = (filing.filing_date.month, filing.filing_date.day)
            if fd >= ev and filing.filing_date.month - ev[0] <= 1:
                s += 8
    return s


class DocumentRouter:
    """Ranks the corpus for a question and returns the top-k candidate filings.

    Deliberately generous (top-4, D11): the router is a recall stage, and
    verification is the precision gate. Widening beyond 4 buys nothing - the
    gold filing is in top-4 for 134/136 and in top-8 for the same 134/136, which
    is why escalation deepens inside the candidates instead (Decision E, §20.1).
    """

    def __init__(
        self,
        catalog: list[FilingMeta],
        aliases: dict[str, list[str]],
        *,
        top_k: int = 4,
        clarify_when_no_company: bool = True,
        prefer_coverage_years: bool = True,
    ) -> None:
        self.catalog = catalog
        self.aliases = aliases
        self.top_k = top_k
        self.clarify_when_no_company = clarify_when_no_company
        self.prefer_coverage_years = prefer_coverage_years
        self.max_year: dict[str, int] = {}
        for f in catalog:
            prev = self.max_year.get(f.company_slug)
            if prev is None or f.fiscal_year > prev:
                self.max_year[f.company_slug] = f.fiscal_year

    def route(self, question: str) -> RouteResult:
        scored = [
            (
                f,
                score(
                    question,
                    f,
                    self.aliases,
                    self.max_year,
                    prefer_coverage_years=self.prefer_coverage_years,
                ),
            )
            for f in self.catalog
        ]
        alive = [(f, sc) for f, sc in scored if sc > ELIMINATED]

        # Ties must not break alphabetically - that was a measured bug. Sort by
        # score, then most recent filing, then doc_id for determinism.
        alive.sort(key=lambda t: (-t[1], -t[0].fiscal_year, t[0].doc_id))

        company_named = bool(alive)
        candidates = [f for f, _ in alive[: self.top_k]]

        # D1: a wrong document is -1, a clarifying question is free.
        needs_clarification = self.clarify_when_no_company and not company_named
        return RouteResult(
            candidates=candidates,
            scores={f.doc_id: sc for f, sc in alive},
            company_named=company_named,
            years=years_in(question),
            needs_clarification=needs_clarification,
            reason="no company named in the question" if needs_clarification else "",
        )


def load_aliases(path) -> dict[str, list[str]]:
    """Load data/company_aliases.yaml.

    ⚠️ That table is measured: it closes the 14/136 questions that name a
    company only by an alias (AMEX, JnJ, JPM) and is part of what produced
    94.1% top-1. Do not regenerate it from company names.
    """
    import yaml

    with open(path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    out: dict[str, list[str]] = {}
    for slug, aliases in raw.items():
        key = str(slug).upper()
        vals = {str(a).lower() for a in aliases}
        vals.add(key.lower())
        out[key] = sorted(vals)
    return out
