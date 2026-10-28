"""Run the validation suite for one month.

SQL checks live in ``sql/checks/*.sql``; each returns rows shaped
``(check_name, severity, passed, metric, threshold, detail)``. Checks that need history
(row counts, null-rate bands) are computed here from the manifest.
"""

from __future__ import annotations

import datetime as dt
import logging
import statistics
from pathlib import Path

import duckdb

from roundtrip.config import Settings, month_bounds
from roundtrip.db import read_sql
from roundtrip.ingest.manifest import Manifest
from roundtrip.validate.report import CheckResult, ValidationReport
from roundtrip.validate.schema import compare_schema

log = logging.getLogger(__name__)

SQL_CHECKS: tuple[str, ...] = (
    "checks/timestamps.sql",
    "checks/money.sql",
    "checks/zones.sql",
    "checks/bases.sql",
)


def _sql_params(settings: Settings, month: str) -> dict[str, str | int | float]:
    start, end = month_bounds(month)
    v = settings.validation
    return {
        "month": month,
        "month_start": start.isoformat(),
        "month_end": end.isoformat(),
        "slack_days": v.timestamp_slack_days,
        "max_wait_seconds": v.max_wait_seconds,
        "max_trip_hours": v.max_trip_hours,
        "max_flagged_share": v.max_flagged_share,
        "max_pickup_before_request_share": v.max_pickup_before_request_share,
        "fare_p999_min": v.fare_p999_min,
        "fare_p999_max": v.fare_p999_max,
        "pay_p999_min": v.pay_p999_min,
        "pay_p999_max": v.pay_p999_max,
        "max_unknown_zone_share": v.max_unknown_zone_share,
        "max_unknown_base_share": v.max_unknown_base_share,
    }


def run_sql_checks(
    con: duckdb.DuckDBPyConnection, settings: Settings, month: str
) -> list[CheckResult]:
    params = _sql_params(settings, month)
    results: list[CheckResult] = []
    for rel in SQL_CHECKS:
        rows = con.execute(read_sql(settings, rel, **params)).fetchall()
        for name, severity, passed, metric, threshold, detail in rows:
            results.append(
                CheckResult(
                    name=str(name),
                    severity=severity,
                    passed=bool(passed),
                    metric=None if metric is None else float(metric),
                    threshold=None if threshold is None else float(threshold),
                    detail=str(detail or ""),
                ),
            )
    return results


def null_rates(con: duckdb.DuckDBPyConnection, columns: list[str]) -> dict[str, float]:
    exprs = ", ".join(f'avg(CASE WHEN "{c}" IS NULL THEN 1.0 ELSE 0.0 END)' for c in columns)
    row = con.execute(f"SELECT {exprs} FROM trips_raw").fetchone()
    assert row is not None
    return {c: float(v) for c, v in zip(columns, row, strict=True)}


def schema_check(month: str, columns: dict[str, str]) -> list[CheckResult]:
    diff = compare_schema(month, columns)
    out = [
        CheckResult(
            name="schema_required_columns",
            severity="hard",
            passed=diff.ok,
            detail=(
                "ok"
                if diff.ok
                else f"missing={list(diff.missing)} mismatched={list(diff.mismatched)}"
            ),
        ),
        CheckResult(
            name="schema_no_unexpected_columns",
            severity="soft",
            passed=not diff.extra,
            detail="ok" if not diff.extra else f"extra columns: {list(diff.extra)}",
        ),
    ]
    return out


def row_count_check(settings: Settings, manifest: Manifest, month: str, n: int) -> CheckResult:
    """Within +/- tolerance of the median over the trailing processed months."""
    v = settings.validation
    history = manifest.row_counts_before(month)
    if len(history) < v.row_count_min_history:
        return CheckResult(
            name="row_count_vs_history",
            severity="soft",
            passed=True,
            metric=float(n),
            detail=f"{n:,} rows; only {len(history)} prior months, band not enforced",
        )
    med = statistics.median(history)
    dev = abs(n - med) / med
    return CheckResult(
        name="row_count_vs_history",
        severity="hard",
        passed=dev <= v.row_count_tolerance,
        metric=dev,
        threshold=v.row_count_tolerance,
        detail=f"{n:,} rows vs trailing median {int(med):,} over {len(history)} months",
    )


def null_rate_checks(
    settings: Settings,
    manifest: Manifest,
    month: str,
    rates: dict[str, float],
) -> list[CheckResult]:
    """Each column's null rate must lie in [min, max] of history, widened by a margin."""
    v = settings.validation
    history = manifest.null_rates_before(month)
    if len(history) < v.null_rate_min_history:
        return [
            CheckResult(
                name="null_rates_vs_history",
                severity="soft",
                passed=True,
                detail=f"only {len(history)} prior months; bands not enforced",
            ),
        ]
    out: list[CheckResult] = []
    for col, rate in rates.items():
        past = [h[col] for h in history if col in h]
        if not past:
            continue
        lo = max(0.0, min(past) - v.null_rate_band_margin)
        hi = min(1.0, max(past) + v.null_rate_band_margin)
        ok = lo <= rate <= hi
        if not ok:
            out.append(
                CheckResult(
                    name=f"null_rate_band:{col}",
                    severity="hard",
                    passed=False,
                    metric=rate,
                    detail=f"null rate {rate:.4f} outside [{lo:.4f}, {hi:.4f}]",
                ),
            )
    if not out:
        out.append(
            CheckResult(
                name="null_rates_vs_history",
                severity="hard",
                passed=True,
                detail=f"all {len(rates)} columns within historical bands",
            ),
        )
    return out


def run_validation(
    con: duckdb.DuckDBPyConnection,
    settings: Settings,
    manifest: Manifest,
    month: str,
    columns: dict[str, str],
) -> ValidationReport:
    """Run every check for ``month``. Requires ``trips_raw``, ``trips`` and ``zones``."""
    row = con.execute("SELECT count(*) FROM trips_raw").fetchone()
    assert row is not None
    n = int(row[0])
    rates = null_rates(con, list(columns))

    checks: list[CheckResult] = []
    checks += schema_check(month, columns)
    checks.append(row_count_check(settings, manifest, month, n))
    checks += null_rate_checks(settings, manifest, month, rates)
    checks += run_sql_checks(con, settings, month)

    report = ValidationReport(
        month=month,
        generated_at=dt.datetime.now(dt.UTC),
        schema_version=compare_schema(month, columns).version,
        row_count=n,
        null_rates=rates,
        checks=checks,
    )
    for c in report.failed:
        log.warning("check %s: %s (%s)", c.name, c.outcome, c.detail)
    return report


def write_report(report: ValidationReport, json_path: Path, md_path: Path) -> None:
    report.write(json_path, md_path)
