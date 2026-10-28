"""DuckDB connection helpers and SQL template loading.

All transforms are ``.sql`` files under ``sql/`` rendered with ``string.Template``
(``${name}`` placeholders) so that they stay readable and reviewable as SQL.
"""

from __future__ import annotations

from pathlib import Path
from string import Template
from typing import Any

import duckdb

from roundtrip.config import Settings


def connect(settings: Settings, path: Path | None = None) -> duckdb.DuckDBPyConnection:
    """Open a DuckDB connection with the configured memory limit and thread count.

    ``path=None`` gives an in-memory database, which is what the per-month pipeline uses:
    nothing from the raw file needs to outlive the job.
    """
    con = duckdb.connect(str(path) if path else ":memory:")
    con.execute(f"SET memory_limit = '{settings.duckdb_memory_limit}'")
    con.execute(f"SET threads = {int(settings.duckdb_threads)}")
    con.execute("SET preserve_insertion_order = false")
    return con


def read_sql(settings: Settings, relative: str, **params: Any) -> str:
    """Load ``sql/<relative>`` and substitute ``${placeholders}``."""
    text = (settings.sql_dir / relative).read_text(encoding="utf-8")
    return Template(text).substitute(**params)


def sql_path_literal(path: Path) -> str:
    """Quote a filesystem path for use inside a SQL string literal."""
    return "'" + path.as_posix().replace("'", "''") + "'"


def parquet_columns(con: duckdb.DuckDBPyConnection, path: Path) -> dict[str, str]:
    """Column name -> DuckDB type for a parquet file, in file order."""
    rows = con.execute(
        f"DESCRIBE SELECT * FROM read_parquet({sql_path_literal(path)})",
    ).fetchall()
    return {str(r[0]): str(r[1]) for r in rows}


def base_case_expression(base_codes: dict[str, str], column: str = "hvfhs_license_num") -> str:
    """``CASE`` mapping licence numbers to base names, unknown codes -> 'Other'."""
    whens = " ".join(f"WHEN '{code}' THEN '{name}'" for code, name in base_codes.items())
    return f"CASE {column} {whens} ELSE 'Other' END"


def register_trips(
    con: duckdb.DuckDBPyConnection,
    settings: Settings,
    parquet_path: Path,
) -> dict[str, str]:
    """Create the ``trips_raw`` and ``trips`` views over one month's parquet file.

    Returns the raw column -> type mapping so callers can record it in the manifest.
    ``trips`` normalises names and derives ``wait_seconds``; it tolerates the absence of
    ``cbd_congestion_fee`` (files before 2025) by filling it with NULL.
    """
    columns = parquet_columns(con, parquet_path)
    con.execute(
        "CREATE OR REPLACE VIEW trips_raw AS SELECT * FROM "
        f"read_parquet({sql_path_literal(parquet_path)}, file_row_number = true)",
    )
    cbd_expr = "cbd_congestion_fee" if "cbd_congestion_fee" in columns else "NULL::DOUBLE"
    con.execute(
        read_sql(
            settings,
            "views/trips.sql",
            base_case=base_case_expression(settings.base_codes),
            cbd_fee_expr=cbd_expr,
        ),
    )
    return columns


def register_zones(con: duckdb.DuckDBPyConnection, settings: Settings, csv_path: Path) -> None:
    """Create the ``zones`` table from the TLC taxi zone lookup CSV."""
    cbd = ", ".join(str(z) for z in settings.cbd_zones)
    airports = ", ".join(str(z) for z in settings.airport_zones)
    con.execute(
        read_sql(
            settings,
            "views/zones.sql",
            csv_path=sql_path_literal(csv_path),
            cbd_zones=cbd,
            airport_zones=airports,
        ),
    )
