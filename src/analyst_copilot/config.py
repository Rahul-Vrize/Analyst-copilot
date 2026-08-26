"""Central configuration. Reads from environment / .env; no hidden defaults
for anything that affects evidence correctness (paths are explicit and
inspectable, per the project's fail-closed philosophy)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    data_dir: Path = field(
        default_factory=lambda: Path(
            os.environ.get("ANALYST_COPILOT_DATA_DIR", PROJECT_ROOT / "data")
        )
    )
    llm_model: str = field(
        default_factory=lambda: os.environ.get(
            "ANALYST_COPILOT_LLM_MODEL", "claude-sonnet-5"
        )
    )
    anthropic_api_key: str | None = field(
        default_factory=lambda: os.environ.get("ANTHROPIC_API_KEY")
    )

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def filings_dir(self) -> Path:
        return self.data_dir / "filings"

    @property
    def practice_dir(self) -> Path:
        return self.data_dir / "practice"

    @property
    def sqlite_path(self) -> Path:
        return self.cache_dir / "analyst_copilot.sqlite"

    @property
    def duckdb_path(self) -> Path:
        return self.cache_dir / "analyst_copilot.duckdb"

    def ensure_dirs(self) -> None:
        for d in (self.raw_dir, self.cache_dir, self.filings_dir, self.practice_dir):
            d.mkdir(parents=True, exist_ok=True)


settings = Settings()

# Exact refusal string required by the scoring rubric. Never paraphrase this.
NOT_FOUND = "Not found in this filing."
