from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from roundtrip.config import Settings
from roundtrip.db import connect, register_trips, register_zones
from roundtrip.ingest.manifest import Manifest, MonthRecord
from roundtrip.validate import ValidationReport, run_validation
from tests.conftest import make_month_parquet, write_zone_lookup


def _validate(
    settings: Settings, month: str, parquet: Path, manifest: Manifest | None = None
) -> ValidationReport:
    zones = write_zone_lookup(settings.zone_lookup_path)
    con = connect(settings)
    try:
        register_zones(con, settings, zones)
        columns = register_trips(con, settings, parquet)
        return run_validation(con, settings, manifest or Manifest(), month, columns)
    finally:
        con.close()


def _by_name(report: ValidationReport) -> dict[str, bool]:
    return {c.name: c.passed for c in report.checks}


def test_clean_month_passes(settings: Settings, tmp_path: Path) -> None:
    p = make_month_parquet(tmp_path / "clean.parquet", "2024-12")
    report = _validate(settings, "2024-12", p)
    assert report.status == "pass", [c for c in report.checks if not c.passed]
    assert report.row_count == 20_000
    assert report.schema_version == "hvfhv-2019"
    assert set(report.null_rates) >= {"driver_pay", "on_scene_datetime"}


def test_clean_2025_month_with_cbd_fee_passes(settings: Settings, tmp_path: Path) -> None:
    p = make_month_parquet(tmp_path / "jan.parquet", "2025-01")
    report = _validate(settings, "2025-01", p)
    assert report.status == "pass", [c for c in report.checks if not c.passed]
    assert report.schema_version == "hvfhv-2025-cbd"


@pytest.mark.parametrize(
    ("kwargs", "failing_check"),
    [
        ({"neg_fare_share": 0.10}, "money_fare_nonnegative"),
        ({"outside_month_share": 0.10}, "ts_pickup_within_month"),
        ({"unknown_zone_share": 0.05}, "zones_pickup_in_lookup"),
        ({"unknown_base_share": 0.05}, "bases_known_codes"),
        ({"pickup_before_request_share": 0.10}, "ts_pickup_after_request"),
        ({"drop_columns": ("driver_pay",)}, "schema_required_columns"),
    ],
)
def test_corrupted_month_fails_the_named_check(
    settings: Settings,
    tmp_path: Path,
    kwargs: dict[str, object],
    failing_check: str,
) -> None:
    p = make_month_parquet(tmp_path / "bad.parquet", "2024-12", **kwargs)  # type: ignore[arg-type]
    if "drop_columns" in kwargs:
        # trips view cannot be built without the column; schema check alone must catch it
        from roundtrip.db import parquet_columns
        from roundtrip.validate.runner import schema_check

        con = connect(settings)
        cols = parquet_columns(con, p)
        con.close()
        results = {c.name: c for c in schema_check("2024-12", cols)}
        assert not results[failing_check].passed
        assert results[failing_check].severity == "hard"
        return
    report = _validate(settings, "2024-12", p)
    outcomes = _by_name(report)
    assert outcomes[failing_check] is False
    assert report.status == "fail"


def test_small_corruption_below_threshold_is_tolerated(settings: Settings, tmp_path: Path) -> None:
    p = make_month_parquet(tmp_path / "mild.parquet", "2024-12", neg_fare_share=0.002)
    report = _validate(settings, "2024-12", p)
    assert _by_name(report)["money_fare_nonnegative"] is True
    assert report.status == "pass"


def _record(month: str, rows: int, null_rates: dict[str, float] | None = None) -> MonthRecord:
    return MonthRecord(
        month=month,
        url="file:///x",
        size_bytes=1,
        sha256="0" * 64,
        row_count=rows,
        columns={},
        null_rates=null_rates or {},
        processed_at=dt.datetime(2025, 1, 1, tzinfo=dt.UTC),
        package_version="0",
        duckdb_version="0",
        validation_status="pass",
    )


def test_row_count_check_uses_trailing_median(settings: Settings, tmp_path: Path) -> None:
    p = make_month_parquet(tmp_path / "m.parquet", "2024-12", n=5_000)
    manifest = Manifest()
    for m in ("2024-09", "2024-10", "2024-11"):
        manifest.months[m] = _record(m, 20_000)
    report = _validate(settings, "2024-12", p, manifest)
    rc = {c.name: c for c in report.checks}["row_count_vs_history"]
    assert rc.severity == "hard" and rc.passed is False
    assert rc.metric == pytest.approx(0.75)
    assert report.status == "fail"


def test_row_count_check_soft_without_history(settings: Settings, tmp_path: Path) -> None:
    p = make_month_parquet(tmp_path / "m.parquet", "2024-12", n=5_000)
    report = _validate(settings, "2024-12", p, Manifest())
    rc = {c.name: c for c in report.checks}["row_count_vs_history"]
    assert rc.severity == "soft" and rc.passed


def test_null_rate_band_flags_new_nulls(settings: Settings, tmp_path: Path) -> None:
    p = make_month_parquet(tmp_path / "m.parquet", "2024-12", null_on_scene_share=0.5)
    manifest = Manifest()
    for m in ("2024-09", "2024-10", "2024-11"):
        manifest.months[m] = _record(m, 20_000, {"on_scene_datetime": 0.0, "driver_pay": 0.0})
    report = _validate(settings, "2024-12", p, manifest)
    names = [c.name for c in report.checks if not c.passed]
    assert "null_rate_band:on_scene_datetime" in names
    assert report.status == "fail"


def test_report_markdown_lists_every_check(settings: Settings, tmp_path: Path) -> None:
    p = make_month_parquet(tmp_path / "m.parquet", "2024-12", n=2_000)
    report = _validate(settings, "2024-12", p)
    md = report.to_markdown()
    assert "# Validation report: 2024-12" in md
    for c in report.checks:
        assert f"`{c.name}`" in md
    out_json, out_md = tmp_path / "v.json", tmp_path / "v.md"
    report.write(out_json, out_md)
    assert ValidationReport.model_validate_json(out_json.read_text()).row_count == 2_000
