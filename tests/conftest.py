"""Shared fixtures: synthetic HVFHV months, a small zone lookup, isolated settings.

Fixture parquet files are generated on the fly with DuckDB (deterministic seed) rather than
committed as binaries, so corruptions can be parameterised in tests.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import duckdb
import pytest

from roundtrip.config import Settings, get_settings, month_bounds

ZONE_LOOKUP_CSV = """\
"LocationID","Borough","Zone","service_zone"
1,"EWR","Newark Airport","EWR"
4,"Manhattan","Alphabet City","Yellow Zone"
7,"Queens","Astoria","Boro Zone"
13,"Manhattan","Battery Park City","Yellow Zone"
37,"Brooklyn","Bushwick South","Boro Zone"
61,"Brooklyn","Crown Heights North","Boro Zone"
132,"Queens","JFK Airport","Airports"
138,"Queens","LaGuardia Airport","Airports"
161,"Manhattan","Midtown Center","Yellow Zone"
230,"Manhattan","Times Sq/Theatre District","Yellow Zone"
236,"Manhattan","Upper East Side North","Yellow Zone"
264,"Unknown","NV","N/A"
265,"N/A","Outside of NYC","N/A"
"""

KNOWN_ZONES = (1, 4, 7, 13, 37, 61, 132, 138, 161, 230, 236)


def write_zone_lookup(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(ZONE_LOOKUP_CSV, encoding="utf-8")
    return path


def make_month_parquet(
    path: Path,
    month: str,
    n: int = 20_000,
    *,
    seed: float = 0.42,
    with_cbd_fee: bool | None = None,
    neg_fare_share: float = 0.0,
    outside_month_share: float = 0.0,
    unknown_zone_share: float = 0.0,
    unknown_base_share: float = 0.0,
    pickup_before_request_share: float = 0.0,
    drop_columns: tuple[str, ...] = (),
    null_on_scene_share: float = 0.0,
) -> Path:
    """Write a synthetic month of HVFHV trips shaped like the real TLC files."""
    start, end = month_bounds(month)
    days = (end - start).days
    if with_cbd_fee is None:
        with_cbd_fee = month >= "2025-01"
    zones = ", ".join(str(z) for z in KNOWN_ZONES)
    con = duckdb.connect()
    con.execute(f"SELECT setseed({seed})")
    con.execute(
        f"""
        CREATE TABLE gen AS
        WITH base AS (
            SELECT
                i,
                random() AS u_base, random() AS u_pu, random() AS u_do, random() AS u_t,
                random() AS u_wait, random() AS u_miles, random() AS u_corrupt, random() AS u_c2,
                random() AS u_c3, random() AS u_c4, random() AS u_c5, random() AS u_tip,
                random() AS u_shared, random() AS u_scene
            FROM range({n}) t(i)
        ),
        shaped AS (
            SELECT
                i,
                CASE WHEN u_c4 < {unknown_base_share} THEN 'HV0099'
                     WHEN u_base < 0.72 THEN 'HV0003' ELSE 'HV0005' END AS hvfhs_license_num,
                CASE WHEN u_c3 < {unknown_zone_share} THEN 999
                     ELSE ([{zones}])[1 + CAST(floor(u_pu * {len(KNOWN_ZONES)}) AS INTEGER)] END AS pu,
                ([{zones}])[1 + CAST(floor(u_do * {len(KNOWN_ZONES)}) AS INTEGER)] AS do_,
                TIMESTAMP '{start.isoformat()}'
                    + to_seconds(CAST(floor(u_t * {days} * 86400) AS BIGINT)) AS req,
                CAST(30 + u_wait * 600 AS BIGINT) AS wait_s,
                0.5 + u_miles * 12 AS miles,
                u_c2 < {outside_month_share} AS outside,
                u_corrupt < {neg_fare_share} AS neg_fare,
                u_c5 < {pickup_before_request_share} AS pickup_before,
                u_tip, u_shared, u_scene
            FROM base
        )
        SELECT
            hvfhs_license_num,
            'B03404' AS dispatching_base_num,
            'B03404' AS originating_base_num,
            req AS request_datetime,
            CASE WHEN u_scene < {null_on_scene_share} THEN NULL
                 ELSE req + to_seconds(wait_s - 10) END AS on_scene_datetime,
            CASE WHEN outside THEN req + INTERVAL 45 DAY
                 WHEN pickup_before THEN req - INTERVAL 10 MINUTE
                 ELSE req + to_seconds(wait_s) END AS pickup_datetime,
            CASE WHEN outside THEN req + INTERVAL 45 DAY + to_seconds(wait_s + CAST(miles * 240 AS BIGINT))
                 ELSE req + to_seconds(wait_s + CAST(miles * 240 AS BIGINT)) END AS dropoff_datetime,
            CAST(pu AS BIGINT) AS PULocationID,
            CAST(do_ AS BIGINT) AS DOLocationID,
            round(miles, 2) AS trip_miles,
            CAST(miles * 240 AS BIGINT) AS trip_time,
            CASE WHEN neg_fare THEN -5.0 ELSE round(6.0 + 2.8 * miles, 2) END AS base_passenger_fare,
            0.0 AS tolls,
            round((6.0 + 2.8 * miles) * 0.0275, 2) AS bcf,
            round((6.0 + 2.8 * miles) * 0.08875, 2) AS sales_tax,
            CASE WHEN pu IN (4, 13, 161, 230, 236) THEN 2.75 ELSE 0.0 END AS congestion_surcharge,
            CASE WHEN pu IN (132, 138, 1) OR do_ IN (132, 138, 1) THEN 2.5 ELSE 0.0 END AS airport_fee,
            CASE WHEN u_tip < 0.2 THEN round(2.0 + u_tip * 10, 2) ELSE 0.0 END AS tips,
            round(4.5 + 2.0 * miles, 2) AS driver_pay,
            CASE WHEN u_shared < 0.1 THEN 'Y' ELSE 'N' END AS shared_request_flag,
            CASE WHEN u_shared < 0.04 THEN 'Y' ELSE 'N' END AS shared_match_flag,
            'N' AS access_a_ride_flag,
            'N' AS wav_request_flag,
            'N' AS wav_match_flag
            {", CASE WHEN pu IN (4, 13, 161, 230) THEN 1.5 ELSE 0.0 END AS cbd_congestion_fee" if with_cbd_fee else ""}
        FROM shaped
        ORDER BY request_datetime
        """,
    )
    cols = [r[0] for r in con.execute("DESCRIBE gen").fetchall()]
    keep = [c for c in cols if c not in drop_columns]
    path.parent.mkdir(parents=True, exist_ok=True)
    con.execute(
        f"COPY (SELECT {', '.join(keep)} FROM gen) TO '{path.as_posix()}' (FORMAT PARQUET)",
    )
    con.close()
    return path


@pytest.fixture
def source_dir(tmp_path: Path) -> Path:
    """A directory laid out like the TLC CDN, served over ``file://``."""
    root = tmp_path / "cdn"
    (root / "trip-data").mkdir(parents=True)
    write_zone_lookup(root / "misc" / "taxi_zone_lookup.csv")
    return root


@pytest.fixture
def settings(
    tmp_path: Path, source_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Settings]:
    monkeypatch.setenv("ROUNDTRIP_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ROUNDTRIP_TLC_BASE_URL", source_dir.as_uri())
    monkeypatch.setenv("ROUNDTRIP_START_MONTH", "2024-11")
    monkeypatch.setenv("ROUNDTRIP_END_MONTH", "2025-02")
    monkeypatch.setenv("ROUNDTRIP_DUCKDB_MEMORY_LIMIT", "1GB")
    monkeypatch.setenv("ROUNDTRIP_DUCKDB_THREADS", "2")
    monkeypatch.setenv("ROUNDTRIP_SAMPLE_ROWS_PER_STRATUM", "20")
    # synthetic fares are small; keep the p99.9 bands meaningful for the fixture
    monkeypatch.setenv("ROUNDTRIP_VALIDATION__FARE_P999_MIN", "10")
    monkeypatch.setenv("ROUNDTRIP_VALIDATION__PAY_P999_MIN", "10")
    for key in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET"):
        monkeypatch.delenv(key, raising=False)
    get_settings.cache_clear()
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    yield s
    get_settings.cache_clear()


@pytest.fixture
def clean_month(source_dir: Path) -> tuple[str, Path]:
    month = "2024-12"
    path = make_month_parquet(source_dir / "trip-data" / f"fhvhv_tripdata_{month}.parquet", month)
    return month, path
