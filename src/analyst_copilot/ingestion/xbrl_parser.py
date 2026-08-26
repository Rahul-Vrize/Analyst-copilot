"""Inline XBRL fact extraction via Arelle (design doc: "Arelle is a
practical open-source option ... Inline XBRL 1.1, XBRL dimensions,
taxonomies" [13]).

STATUS: Phase 2 — not yet implemented. Wire this up before relying on
tagged-fact lookup (`find_facts`); until then, all numeric answers must come
from the parsed table cells in html_parser.py, which are strictly less
reliable than tagged XBRL facts for unit/scale/sign.

Planned shape:

    def extract_xbrl_facts(raw_html_path: Path, filing_id: str) -> list[XbrlFact]:
        # run Arelle's Cntlr against the filing, walk model.factsInInstance,
        # and return one XbrlFact per tagged fact with qname, context,
        # dimensions, period, unit, decimals, scale, sign, value, and the
        # source ix element's DOM xpath (so it can double as an EvidenceLocator).

Requires the optional `xbrl` extra: `pip install -e ".[xbrl]"`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class XbrlFact:
    filing_id: str
    block_id: str | None  # source ix element, if it can be matched back to a block
    qname: str
    context_id: str | None
    dimensions: str | None  # JSON-encoded dimension/member pairs
    period_start: str | None
    period_end: str | None
    unit: str | None
    decimals: int | None
    scale: str | None
    sign: int | None
    value: float | None


def extract_xbrl_facts(raw_html_path: Path, filing_id: str) -> list[XbrlFact]:
    raise NotImplementedError(
        "XBRL extraction is not wired up yet (Phase 2). Install the `xbrl` "
        "extra and implement this against Arelle's Python API."
    )
