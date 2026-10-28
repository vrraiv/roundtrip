"""The data manifest: what was processed, from which bytes, with which code.

The manifest is the only record of the raw files, because the raw files themselves are
not stored. It lives at ``derived/manifest.json`` and is mirrored to R2 with the rest of
the derived outputs; a copy is committed to git in each candidate PR for the audit trail.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

MANIFEST_VERSION = 1

ValidationStatus = Literal["pass", "warn", "fail", "pending"]


class ZoneLookupRecord(BaseModel):
    url: str
    size_bytes: int
    sha256: str
    fetched_at: dt.datetime
    n_zones: int


class MonthRecord(BaseModel):
    """One processed month of raw trip data."""

    month: str
    url: str
    size_bytes: int
    sha256: str
    etag: str | None = None
    last_modified: str | None = None
    row_count: int
    columns: dict[str, str]  # raw column name -> DuckDB type, in file order
    null_rates: dict[str, float] = Field(default_factory=dict)
    processed_at: dt.datetime
    package_version: str
    duckdb_version: str
    git_sha: str | None = None
    validation_status: ValidationStatus = "pending"
    revision_of: str | None = None  # previous sha256 if this month was reprocessed


class Manifest(BaseModel):
    version: int = MANIFEST_VERSION
    dataset: str = "fhvhv"
    months: dict[str, MonthRecord] = Field(default_factory=dict)
    zone_lookup: ZoneLookupRecord | None = None

    # --- persistence ---------------------------------------------------------------------
    @classmethod
    def load(cls, path: Path) -> Manifest:
        if not path.exists():
            return cls()
        return cls.model_validate_json(path.read_text(encoding="utf-8"))

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json(), encoding="utf-8")

    def to_json(self) -> str:
        data = self.model_dump(mode="json")
        data["months"] = dict(sorted(data["months"].items()))
        return json.dumps(data, indent=2, sort_keys=False) + "\n"

    def content_hash(self) -> str:
        """SHA-256 over the canonical JSON; recorded in every ``run.json``."""
        return hashlib.sha256(self.to_json().encode("utf-8")).hexdigest()

    # --- queries -------------------------------------------------------------------------
    def processed_months(self) -> list[str]:
        return sorted(self.months)

    def passed_months(self) -> list[str]:
        return sorted(m for m, r in self.months.items() if r.validation_status == "pass")

    def has_month(self, month: str) -> bool:
        return month in self.months

    def row_counts_before(self, month: str, n: int = 12) -> list[int]:
        """Row counts of the ``n`` passed months strictly before ``month``."""
        prior = [m for m in self.passed_months() if m < month]
        return [self.months[m].row_count for m in prior[-n:]]

    def null_rates_before(self, month: str, n: int = 12) -> list[dict[str, float]]:
        """Null rates of the ``n`` passed months strictly before ``month``."""
        prior = [m for m in self.passed_months() if m < month]
        return [self.months[m].null_rates for m in prior[-n:]]


def sha256_file(path: Path, chunk_size: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(chunk_size):
            h.update(chunk)
    return h.hexdigest()
