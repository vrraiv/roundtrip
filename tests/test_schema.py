from __future__ import annotations

from roundtrip.validate.schema import compare_schema, expected_schema, type_family

BASE = {
    "hvfhs_license_num": "VARCHAR",
    "dispatching_base_num": "VARCHAR",
    "originating_base_num": "VARCHAR",
    "request_datetime": "TIMESTAMP",
    "on_scene_datetime": "TIMESTAMP",
    "pickup_datetime": "TIMESTAMP",
    "dropoff_datetime": "TIMESTAMP",
    "PULocationID": "BIGINT",
    "DOLocationID": "BIGINT",
    "trip_miles": "DOUBLE",
    "trip_time": "BIGINT",
    "base_passenger_fare": "DOUBLE",
    "tolls": "DOUBLE",
    "bcf": "DOUBLE",
    "sales_tax": "DOUBLE",
    "congestion_surcharge": "DOUBLE",
    "airport_fee": "DOUBLE",
    "tips": "DOUBLE",
    "driver_pay": "DOUBLE",
    "shared_request_flag": "VARCHAR",
    "shared_match_flag": "VARCHAR",
    "access_a_ride_flag": "VARCHAR",
    "wav_request_flag": "VARCHAR",
    "wav_match_flag": "VARCHAR",
}


def test_schema_version_switches_in_2025() -> None:
    assert expected_schema("2024-12").name == "hvfhv-2019"
    assert expected_schema("2025-01").name == "hvfhv-2025-cbd"
    assert "cbd_congestion_fee" in expected_schema("2025-06").columns


def test_type_families_tolerate_width_changes() -> None:
    assert type_family("INTEGER") == type_family("BIGINT") == "integer"
    assert type_family("TIMESTAMP_NS") == type_family("TIMESTAMP") == "timestamp"
    assert type_family("FLOAT") == type_family("DOUBLE") == "float"
    assert type_family("VARCHAR") == "string"


def test_compare_schema_clean() -> None:
    diff = compare_schema("2024-12", BASE)
    assert diff.ok and not diff.extra


def test_compare_schema_missing_cbd_fee_in_2025_is_a_failure() -> None:
    diff = compare_schema("2025-01", BASE)
    assert not diff.ok
    assert diff.missing == ("cbd_congestion_fee",)


def test_compare_schema_extra_column_is_not_a_failure() -> None:
    diff = compare_schema("2024-12", {**BASE, "new_col": "VARCHAR"})
    assert diff.ok
    assert diff.extra == ("new_col",)


def test_compare_schema_type_mismatch() -> None:
    diff = compare_schema("2024-12", {**BASE, "driver_pay": "VARCHAR"})
    assert not diff.ok
    assert diff.mismatched == (("driver_pay", "float", "string"),)
