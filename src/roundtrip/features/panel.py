"""Build derived outputs for one month from the ``trips`` and ``zones`` relations."""

from __future__ import annotations

import logging
from pathlib import Path

import duckdb

from roundtrip.config import Settings, month_bounds
from roundtrip.db import read_sql, sql_path_literal

log = logging.getLogger(__name__)


def _count_rows(con: duckdb.DuckDBPyConnection, path: Path) -> int:
    row = con.execute(f"SELECT count(*) FROM read_parquet({sql_path_literal(path)})").fetchone()
    assert row is not None
    return int(row[0])


def build_panel(con: duckdb.DuckDBPyConnection, settings: Settings, month: str) -> tuple[Path, int]:
    """Write the zone-hour panel parquet for ``month``; return ``(path, rows)``."""
    start, end = month_bounds(month)
    out = settings.panel_path(month)
    out.parent.mkdir(parents=True, exist_ok=True)
    con.execute(
        read_sql(
            settings,
            "panel/zone_hour.sql",
            month=month,
            month_start=start.isoformat(),
            month_end=end.isoformat(),
            max_wait_seconds=settings.validation.max_wait_seconds,
            out_path=sql_path_literal(out),
        ),
    )
    n = _count_rows(con, out)
    log.info("panel %s: %s rows -> %s", month, f"{n:,}", out)
    return out, n


def build_sample(
    con: duckdb.DuckDBPyConnection, settings: Settings, month: str
) -> tuple[Path, int]:
    """Write the fixed-seed stratified trip sample for ``month``; return ``(path, rows)``."""
    start, end = month_bounds(month)
    out = settings.sample_path(month)
    out.parent.mkdir(parents=True, exist_ok=True)
    con.execute(
        read_sql(
            settings,
            "panel/trip_sample.sql",
            month=month,
            month_start=start.isoformat(),
            month_end=end.isoformat(),
            seed=settings.sample_seed,
            rows_per_stratum=settings.sample_rows_per_stratum,
            out_path=sql_path_literal(out),
        ),
    )
    n = _count_rows(con, out)
    log.info("sample %s: %s rows -> %s", month, f"{n:,}", out)
    return out, n
