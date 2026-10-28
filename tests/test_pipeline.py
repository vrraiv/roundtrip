from __future__ import annotations

import json
from pathlib import Path

import pytest

from roundtrip.config import Settings
from roundtrip.ingest import tlc
from roundtrip.ingest.manifest import Manifest, sha256_file
from roundtrip.pipeline import process_month
from roundtrip.validate import ValidationError
from tests.conftest import make_month_parquet


def test_process_month_end_to_end(settings: Settings, clean_month: tuple[str, Path]) -> None:
    month, src = clean_month
    result = process_month(settings, month)
    assert result.status == "processed"
    assert result.report is not None and result.report.status == "pass"

    assert settings.panel_path(month).exists()
    assert settings.sample_path(month).exists()
    assert settings.validation_path(month, "json").exists()
    assert settings.validation_path(month, "md").exists()
    assert not settings.raw_path(month).exists(), "raw file must be deleted after processing"

    manifest = Manifest.load(settings.manifest_path)
    rec = manifest.months[month]
    assert rec.sha256 == sha256_file(src)
    assert rec.size_bytes == src.stat().st_size
    assert rec.row_count == 20_000
    assert rec.validation_status == "pass"
    assert "cbd_congestion_fee" not in rec.columns
    assert manifest.zone_lookup is not None and manifest.zone_lookup.n_zones == 13

    run_json = [p for p in result.outputs if p.name == "run.json"]
    assert len(run_json) == 1
    payload = json.loads(run_json[0].read_text())
    assert payload["month"] == month
    assert payload["manifest_hash"] == manifest.content_hash()
    assert payload["packages"]["duckdb"]
    assert payload["config"]["sample_seed"] == settings.sample_seed


def test_second_run_is_skipped(settings: Settings, clean_month: tuple[str, Path]) -> None:
    month, _ = clean_month
    assert process_month(settings, month).status == "processed"
    assert process_month(settings, month).status == "skipped"


def test_revision_is_flagged_and_needs_force(
    settings: Settings, clean_month: tuple[str, Path]
) -> None:
    month, src = clean_month
    process_month(settings, month)
    old_sha = Manifest.load(settings.manifest_path).months[month].sha256

    # TLC republishes the month with different bytes
    make_month_parquet(src, month, n=21_000)
    d = tlc.discover(settings, Manifest.load(settings.manifest_path))
    assert d.revised == [month]
    assert d.new == []

    assert process_month(settings, month).status == "skipped"
    result = process_month(settings, month, force=True)
    assert result.status == "processed"
    rec = Manifest.load(settings.manifest_path).months[month]
    assert rec.revision_of == old_sha
    assert rec.row_count == 21_000


def test_discover_classifies_months(settings: Settings, source_dir: Path) -> None:
    make_month_parquet(
        source_dir / "trip-data" / "fhvhv_tripdata_2024-11.parquet", "2024-11", n=500
    )
    make_month_parquet(
        source_dir / "trip-data" / "fhvhv_tripdata_2024-12.parquet", "2024-12", n=500
    )
    process_month(settings, "2024-11")
    d = tlc.discover(settings, Manifest.load(settings.manifest_path))
    assert d.unchanged == ["2024-11"]
    assert d.new == ["2024-12"]
    assert d.unavailable == ["2025-01", "2025-02"]


def test_validation_failure_stops_before_panel(settings: Settings, source_dir: Path) -> None:
    month = "2024-12"
    make_month_parquet(
        source_dir / "trip-data" / f"fhvhv_tripdata_{month}.parquet",
        month,
        neg_fare_share=0.2,
    )
    with pytest.raises(ValidationError) as exc:
        process_month(settings, month)
    assert exc.value.report.status == "fail"
    assert settings.validation_path(month, "md").exists()
    assert not settings.panel_path(month).exists()
    assert Manifest.load(settings.manifest_path).months[month].validation_status == "fail"
    # a failed month is not silently retried as "new"
    assert process_month(settings, month).status == "skipped"


def test_unpublished_month_raises(settings: Settings) -> None:
    with pytest.raises(FileNotFoundError):
        process_month(settings, "2025-02")
