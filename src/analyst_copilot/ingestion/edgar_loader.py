"""Load a filing's raw HTML from a local path (the "Add filing" upload) or a
URL, content-address it, and persist an immutable copy under data/raw/.

This module does not attempt to auto-discover EDGAR metadata (CIK, form
type, period) yet — the caller supplies what it knows, and unknown fields
stay NULL rather than guessed. Auto-lookup via the EDGAR full-text search /
submissions API is a natural Phase 2+ addition (design doc ref [2], [14]).
"""

from __future__ import annotations

import hashlib
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from analyst_copilot.config import settings
from analyst_copilot.ingestion.locator import FilingRecord

PARSER_VERSION = "0.1.0"


def _content_hash(raw_bytes: bytes) -> str:
    return hashlib.sha256(raw_bytes).hexdigest()


def _fetch_bytes(path_or_url: str) -> bytes:
    if path_or_url.startswith(("http://", "https://")):
        req = urllib.request.Request(
            path_or_url, headers={"User-Agent": "analyst-copilot research contact@example.com"}
        )
        with urllib.request.urlopen(req) as resp:  # noqa: S310 - trusted, user-supplied SEC URL
            return resp.read()
    return Path(path_or_url).read_bytes()


def load_filing(
    path_or_url: str,
    *,
    company: str | None = None,
    cik: str | None = None,
    form_type: str | None = None,
    filing_date: str | None = None,
    period: str | None = None,
    accession: str | None = None,
) -> tuple[FilingRecord, bytes]:
    """Fetch raw HTML, store it content-addressed under data/raw/, and
    return the filing metadata record plus the raw bytes for parsing."""
    raw_bytes = _fetch_bytes(path_or_url)
    filing_id = _content_hash(raw_bytes)

    settings.raw_dir.mkdir(parents=True, exist_ok=True)
    raw_path = settings.raw_dir / f"{filing_id}.html"
    if not raw_path.exists():
        raw_path.write_bytes(raw_bytes)

    source_url = path_or_url if path_or_url.startswith(("http://", "https://")) else None

    record = FilingRecord(
        filing_id=filing_id,
        accession=accession,
        company=company,
        cik=cik,
        form_type=form_type,
        filing_date=filing_date,
        period=period,
        source_url=source_url,
        raw_path=str(raw_path),
        parser_version=PARSER_VERSION,
        ingested_at=datetime.now(timezone.utc).isoformat(),
    )
    return record, raw_bytes
