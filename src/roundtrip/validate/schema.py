"""Expected HVFHV schema, versioned by the month it applies from.

TLC changes columns occasionally (``cbd_congestion_fee`` arrived with the 2025 files) and
has said it may standardise types across years. We therefore check *type families*
(string / timestamp / integer / float) rather than exact widths, and record the exact
DuckDB types in the manifest so drift is visible without failing the run.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Family = Literal["string", "timestamp", "integer", "float", "boolean", "other"]

_BASE_COLUMNS: dict[str, Family] = {
    "hvfhs_license_num": "string",
    "dispatching_base_num": "string",
    "originating_base_num": "string",
    "request_datetime": "timestamp",
    "on_scene_datetime": "timestamp",
    "pickup_datetime": "timestamp",
    "dropoff_datetime": "timestamp",
    "PULocationID": "integer",
    "DOLocationID": "integer",
    "trip_miles": "float",
    "trip_time": "integer",
    "base_passenger_fare": "float",
    "tolls": "float",
    "bcf": "float",
    "sales_tax": "float",
    "congestion_surcharge": "float",
    "airport_fee": "float",
    "tips": "float",
    "driver_pay": "float",
    "shared_request_flag": "string",
    "shared_match_flag": "string",
    "access_a_ride_flag": "string",
    "wav_request_flag": "string",
    "wav_match_flag": "string",
}


@dataclass(frozen=True)
class SchemaVersion:
    name: str
    effective_from: str  # YYYY-MM, inclusive
    columns: dict[str, Family]


SCHEMA_VERSIONS: tuple[SchemaVersion, ...] = (
    SchemaVersion("hvfhv-2019", "2019-02", dict(_BASE_COLUMNS)),
    SchemaVersion(
        "hvfhv-2025-cbd",
        "2025-01",
        {**_BASE_COLUMNS, "cbd_congestion_fee": "float"},
    ),
)


def expected_schema(month: str) -> SchemaVersion:
    """The schema version in force for ``month`` (latest ``effective_from`` <= month)."""
    applicable = [v for v in SCHEMA_VERSIONS if v.effective_from <= month]
    if not applicable:
        msg = f"no schema version covers {month}"
        raise ValueError(msg)
    return max(applicable, key=lambda v: v.effective_from)


def type_family(duckdb_type: str) -> Family:
    t = duckdb_type.upper()
    if t.startswith("TIMESTAMP") or t.startswith("DATE"):
        return "timestamp"
    if t in {"VARCHAR", "TEXT", "STRING", "CHAR", "BPCHAR"} or t.startswith("VARCHAR"):
        return "string"
    if t in {"TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT"} or t.startswith("U"):
        return "integer"
    if t in {"FLOAT", "REAL", "DOUBLE"} or t.startswith("DECIMAL") or t.startswith("NUMERIC"):
        return "float"
    if t == "BOOLEAN":
        return "boolean"
    return "other"


@dataclass(frozen=True)
class SchemaDiff:
    version: str
    missing: tuple[str, ...]
    extra: tuple[str, ...]
    mismatched: tuple[tuple[str, Family, Family], ...]  # (column, expected, actual)

    @property
    def ok(self) -> bool:
        return not self.missing and not self.mismatched


def compare_schema(month: str, actual: dict[str, str]) -> SchemaDiff:
    expected = expected_schema(month)
    missing = tuple(c for c in expected.columns if c not in actual)
    extra = tuple(c for c in actual if c not in expected.columns)
    mismatched = tuple(
        (c, fam, type_family(actual[c]))
        for c, fam in expected.columns.items()
        if c in actual and type_family(actual[c]) != fam
    )
    return SchemaDiff(expected.name, missing, extra, mismatched)
