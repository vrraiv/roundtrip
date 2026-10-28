from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd
import pytest

from roundtrip.config import Settings
from roundtrip.db import connect, register_trips, register_zones
from roundtrip.features import build_panel, build_sample
from tests.conftest import make_month_parquet, write_zone_lookup


@pytest.fixture
def con(settings: Settings, tmp_path: Path) -> duckdb.DuckDBPyConnection:
    p = make_month_parquet(tmp_path / "m.parquet", "2024-12", n=30_000, outside_month_share=0.01)
    c = connect(settings)
    register_zones(c, settings, write_zone_lookup(settings.zone_lookup_path))
    register_trips(c, settings, p)
    return c


def test_panel_matches_pandas_recomputation(
    settings: Settings, con: duckdb.DuckDBPyConnection
) -> None:
    path, n = build_panel(con, settings, "2024-12")
    assert path.exists() and n > 0
    panel = pd.read_parquet(path)
    trips = con.execute(
        "SELECT pu_zone, base, date_trunc('hour', pickup_datetime) AS hour_ts, wait_seconds, "
        "base_passenger_fare, driver_pay, tips, shared_request FROM trips "
        "WHERE pickup_datetime >= '2024-12-01' AND pickup_datetime < '2025-01-01'",
    ).df()

    # month filter: trips that spilled into January are not in December's panel
    assert panel["n_trips"].sum() == len(trips)
    assert panel["month"].eq("2024-12").all()
    assert panel.duplicated(["pu_zone", "hour_ts", "base"]).sum() == 0

    # spot check the busiest cell against pandas
    top = panel.sort_values("n_trips", ascending=False).iloc[0]
    cell = trips[
        (trips.pu_zone == top.pu_zone) & (trips.base == top.base) & (trips.hour_ts == top.hour_ts)
    ]
    assert len(cell) == top.n_trips
    assert cell.wait_seconds.mean() == pytest.approx(top.mean_wait_seconds)
    assert cell.base_passenger_fare.mean() == pytest.approx(top.mean_base_fare)
    assert cell.driver_pay.mean() == pytest.approx(top.mean_driver_pay)
    assert cell.tips.sum() / cell.base_passenger_fare.sum() == pytest.approx(top.tip_rate)
    assert cell.shared_request.sum() == top.n_shared_request


def test_panel_flags_airport_trips(settings: Settings, con: duckdb.DuckDBPyConnection) -> None:
    path, _ = build_panel(con, settings, "2024-12")
    panel = pd.read_parquet(path)
    jfk = panel[panel.pu_zone == 132]
    assert (jfk.n_airport_trips == jfk.n_trips).all()


def test_sample_is_deterministic_and_stratified(
    settings: Settings, con: duckdb.DuckDBPyConnection
) -> None:
    path, n1 = build_sample(con, settings, "2024-12")
    first = pd.read_parquet(path)
    path, n2 = build_sample(con, settings, "2024-12")
    second = pd.read_parquet(path)
    assert n1 == n2 > 0
    pd.testing.assert_frame_equal(first, second)

    per_stratum = first.groupby(["base", "pu_borough", first.pickup_datetime.dt.hour]).size()
    assert per_stratum.max() <= settings.sample_rows_per_stratum
    # weights reconstruct the stratum sizes: sum of weights == number of in-month trips
    total = con.execute(
        "SELECT count(*) FROM trips WHERE pickup_datetime >= '2024-12-01' "
        "AND pickup_datetime < '2025-01-01'",
    ).fetchone()
    assert total is not None
    assert first.sample_weight.sum() == pytest.approx(total[0], rel=1e-9)
    assert {"pu_is_cbd", "do_borough", "wait_seconds", "cbd_congestion_fee"} <= set(first.columns)


def test_sample_changes_with_seed(settings: Settings, con: duckdb.DuckDBPyConnection) -> None:
    path, _ = build_sample(con, settings, "2024-12")
    a = pd.read_parquet(path).trip_row.tolist()
    other = settings.model_copy(update={"sample_seed": settings.sample_seed + 1})
    path, _ = build_sample(con, other, "2024-12")
    b = pd.read_parquet(path).trip_row.tolist()
    assert a != b
