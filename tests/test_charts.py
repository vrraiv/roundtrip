"""The two inline SVG charts on the public site."""

from __future__ import annotations

import datetime as dt
import itertools

from roundtrip.report.charts import _nice_ticks, effect_chart, series_chart, weekly


def _days(n: int) -> dict[str, list]:
    start = dt.date(2024, 11, 1)
    days = [(start + dt.timedelta(days=i)).isoformat() for i in range(n)]
    return {
        "day": days,
        "actual": [100.0 + i for i in range(n)],
        "synthetic": [110.0 + i for i in range(n)],
    }


def test_weekly_means_drop_a_short_tail() -> None:
    weeks = weekly(_days(17))  # two full weeks and a 3-day tail
    assert [w["week"] for w in weeks] == ["2024-11-01", "2024-11-08"]
    assert weeks[0]["actual"] == 103.0  # mean of 100..106
    assert weeks[1]["synthetic"] == 120.0
    assert weekly({"day": [], "actual": [], "synthetic": []}) == []


def test_nice_ticks_span_the_range() -> None:
    ticks = _nice_ticks(-17.0, 12.0, 5)
    assert ticks[0] <= -17.0 and ticks[-1] >= 12.0
    assert 0.0 in ticks
    assert len({round(b - a, 6) for a, b in itertools.pairwise(ticks)}) == 1


def test_effect_chart_marks_and_table() -> None:
    rows = [
        {"design": "prior_year", "effect_pct": -5.0, "lo_pct": -8.0, "hi_pct": -2.0},
        {"design": "synthetic_control", "effect_pct": -6.5, "lo_pct": -15.0, "hi_pct": 2.0},
        {"design": "pre_window", "effect_pct": None, "lo_pct": None, "hi_pct": None},
    ]
    svg = effect_chart([("Trips", rows), ("Waits", [])])
    assert svg.count('class="mark"') == 2  # the row without an estimate is skipped
    assert 'data-effect="-6.5%"' in svg
    assert 'data-interval="-15.0% to +2.0%"' in svg
    assert ">0%<" in svg and "+0%" not in svg
    assert "<td>synthetic control</td><td>-6.5%</td><td>[-15.0, +2.0]</td>" in svg
    assert "Waits" not in svg
    assert effect_chart([("Trips", [])]) == ""


def test_series_chart_shades_windows_and_lists_weeks() -> None:
    bundle = {
        "synth": {"n_trips": {"series": _days(70)}},
        "windows": {
            "anticipation": {"start": "2024-11-14", "end": "2025-01-04"},
            "post": {"start": "2025-01-05", "end": "2026-01-03"},
        },
    }
    svg = series_chart(bundle, "n_trips", "Trips")
    assert svg.count('class="window"') == 2
    assert ">anticipation<" in svg and "first year of the charge" in svg
    assert svg.count("<polyline") == 2
    assert '"names":["CBD actual","synthetic control"]' in svg
    assert "<td>2024-11-01</td><td>103</td><td>113</td>" in svg
    assert ">2025<" in svg  # the January tick
    # no series, no chart
    assert series_chart({"synth": {"n_trips": {"series": {"day": []}}}}, "n_trips", "T") == ""
    assert series_chart({}, "n_trips", "Trips") == ""
