from __future__ import annotations

import datetime as dt

import pytest

from roundtrip.config import CBD_ZONES, Settings, month_bounds, month_range, validate_month


def test_month_range_inclusive_and_wraps_year() -> None:
    assert month_range("2024-11", "2025-02") == ["2024-11", "2024-12", "2025-01", "2025-02"]
    assert month_range("2025-03", "2025-03") == ["2025-03"]
    assert month_range("2025-04", "2025-03") == []


def test_month_bounds() -> None:
    assert month_bounds("2024-12") == (dt.date(2024, 12, 1), dt.date(2025, 1, 1))
    assert month_bounds("2024-02") == (dt.date(2024, 2, 1), dt.date(2024, 3, 1))


@pytest.mark.parametrize("bad", ["2024-13", "2024-1", "202401", "2024/01", ""])
def test_validate_month_rejects(bad: str) -> None:
    with pytest.raises(ValueError, match="YYYY-MM"):
        validate_month(bad)


def test_cbd_zone_list_matches_mta_dataset() -> None:
    # 38 zones in the MTA "Central Business District Taxi Zones" dataset (yfdc-w5jh).
    assert len(CBD_ZONES) == 38
    assert len(set(CBD_ZONES)) == 38
    assert 163 in CBD_ZONES  # Midtown North straddles 60th St but is included by the MTA
    assert 237 not in CBD_ZONES  # Upper East Side South is north of the line
    assert 43 not in CBD_ZONES  # Central Park


def test_settings_paths_and_urls(settings: Settings) -> None:
    assert settings.raw_url("2024-12").endswith("/trip-data/fhvhv_tripdata_2024-12.parquet")
    assert (
        settings.panel_path("2024-12")
        .as_posix()
        .endswith(
            "derived/panel/zone_hour/month=2024-12/data.parquet",
        )
    )
    assert settings.manifest_path == settings.derived_dir / "manifest.json"
    assert settings.start_month == "2024-11"
    assert not settings.r2.configured


def test_r2_account_id_accepts_endpoint_url() -> None:
    from roundtrip.config import R2Settings

    bare = R2Settings(account_id="0123456789abcdef0123456789abcdef", _env_file=None)
    url = R2Settings(
        account_id="https://0123456789abcdef0123456789abcdef.r2.cloudflarestorage.com/",
        _env_file=None,
    )
    assert url.account_id == bare.account_id
    assert url.endpoint_url == "https://0123456789abcdef0123456789abcdef.r2.cloudflarestorage.com"
