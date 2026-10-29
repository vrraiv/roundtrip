"""The anchored and growth-rate forecast designs, the change-point test and the
rolling-origin validation."""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from roundtrip.causal import forecast

ORIGINS = (dt.date(2023, 6, 30), dt.date(2023, 9, 30), dt.date(2023, 12, 31))
HORIZONS = ((1, 3), (4, 6))
END = dt.date(2024, 5, 14)


def _panel(
    seed: int = 0, step_at: int | None = None, drift_per_day: float = 0.0
) -> tuple[pd.Series, pd.DataFrame]:
    """Treated unit as a noisy combination of 6 donors over 500 days, with an optional
    level step in the treated unit at day ``step_at`` and an optional treated-only
    linear drift."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2023-01-01", periods=500, freq="D")
    t = np.arange(500)
    season = 1 + 0.1 * np.sin(2 * np.pi * t / 365) + 0.05 * (t % 7 == 5)
    donors = pd.DataFrame(
        {z: (800 + 100 * z) * season * (1 + 0.03 * rng.standard_normal(500)) for z in range(6)},
        index=idx,
    )
    treated = 10 * donors[0] + 6 * donors[1] + 300 * rng.standard_normal(500)
    if step_at is not None:
        treated = treated * np.where(t >= step_at, 0.95, 1.0)
    if drift_per_day:
        treated = treated * (1 + drift_per_day * t)
    return pd.Series(treated, index=idx), donors


def test_every_model_forecasts_a_stable_unit() -> None:
    treated, donors = _panel()
    train_end = dt.date(2023, 9, 30)
    for model in forecast.MODELS:
        f = forecast.fit_forecast(model, "n_trips", treated, donors, dt.date(2023, 1, 1), train_end)
        w = f.window(dt.date(2023, 10, 1), dt.date(2024, 5, 14))
        assert abs(w["mean_gap_pct"]) < 2, (model, w)
    g = forecast.fit_forecast(
        "growth", "n_trips", treated, donors, dt.date(2023, 1, 1), train_end
    ).params
    assert g["anchor_days"] == forecast.ANCHOR_DAYS
    assert 10 < g["level_ratio"] < 17  # 13400 over a geometric mean of donors (800 to 1300)
    assert abs(sum(g["weights"].values()) - 1) < 0.02


def test_anchored_and_growth_carry_a_late_training_level_shift_forward() -> None:
    # a 5% drop in the treated unit four months before training ends: the simplex averages
    # it into its weights; the anchored and growth designs carry the late level forward.
    treated, donors = _panel(step_at=150)
    train_end = dt.date(2023, 9, 30)
    target = (dt.date(2023, 10, 1), dt.date(2024, 5, 14))
    gaps = {}
    for model in ("simplex", "anchored", "growth"):
        f = forecast.fit_forecast(model, "n_trips", treated, donors, dt.date(2023, 1, 1), train_end)
        gaps[model] = f.window(*target)["mean_gap_pct"]
    assert gaps["simplex"] < -1.5
    assert abs(gaps["anchored"]) < 1.5
    assert abs(gaps["growth"]) < 1.5


def test_growth_rejects_non_positive_training_values() -> None:
    treated, donors = _panel()
    donors.iloc[10, 2] = 0.0
    try:
        forecast.fit_forecast(
            "growth", "n_trips", treated, donors, dt.date(2023, 1, 1), dt.date(2023, 9, 30)
        )
    except ValueError as exc:
        assert "non-positive" in str(exc)
    else:
        raise AssertionError("expected a ValueError")


def test_change_point_prefers_the_right_model() -> None:
    rng = np.random.default_rng(1)
    idx = pd.date_range("2023-01-01", periods=600, freq="D")
    noise = rng.standard_normal(600)
    step = pd.Series(np.where(np.arange(600) >= 300, -3.0, 0.0) + noise, index=idx)
    r = forecast.change_point(step)
    assert r["preferred"] == "step"
    assert abs(r["shift"] + 3.0) < 0.6
    assert abs((pd.Timestamp(r["break_week"]) - idx[300]).days) <= 10
    assert r["sup_f"] > 50
    flat = forecast.change_point(pd.Series(noise, index=idx))
    assert flat["preferred"] == "constant"
    assert flat["sup_f"] < r["sup_f"]
    line = forecast.change_point(pd.Series(-0.01 * np.arange(600) + noise, index=idx))
    assert line["preferred"] in ("linear", "step_and_trend")
    assert line["slope_per_month"] < -0.2
    assert forecast.change_point(pd.Series(noise[:50], index=idx[:50])) == {"n_weeks": 8}


def test_verdict_only_counts_design_models() -> None:
    block = {
        "models": {
            m: {
                "windows": {
                    "holdout": {"mean_gap_pct": g},
                    "holdout_late": {"mean_gap_pct": g},
                }
            }
            for m, g in (("simplex", -5.0), ("ridge", -4.0), ("growth", 0.5))
        }
    }
    v = forecast.drift_verdict(block, 3.0)
    assert v["growth"]["passes"] and not v["growth"]["counts"]
    assert v["simplex"]["counts"] and not v["simplex"]["passes"]
    assert not forecast.verdict_passes(v)


def test_horizon_windows_follow_the_calendar_and_stop_at_the_end() -> None:
    o = dt.date(2023, 9, 30)
    assert forecast.horizon_window(o, (1, 3), END) == (dt.date(2023, 10, 1), dt.date(2023, 12, 31))
    assert forecast.horizon_window(o, (4, 6), END) == (dt.date(2024, 1, 1), dt.date(2024, 3, 31))
    assert forecast.horizon_window(o, (7, 12), END) == (dt.date(2024, 4, 1), END)
    assert forecast.horizon_window(dt.date(2024, 4, 30), (1, 3), END) == (
        dt.date(2024, 5, 1),
        END,
    )
    assert forecast.horizon_window(dt.date(2024, 4, 30), (4, 6), END) is None


def test_rolling_origin_passes_a_stable_unit_and_fails_a_drifting_one() -> None:
    treated, donors = _panel()
    block = forecast.rolling_origin(
        "n_trips", treated, donors, dt.date(2023, 1, 1), ORIGINS, HORIZONS, END, 28, 2.0
    )
    assert set(block["summary"]) == set(forecast.MODELS)
    assert block["holiday_windows"] == 2  # Jun origin 4-6 and Sep origin 1-3 hold Dec 2023
    for m, s in block["summary"].items():
        assert s["passes"], (m, s)
        assert s["horizons"]["1-3"]["n_origins"] == 3
        assert s["horizons"]["4-6"]["n_origins"] == 3
        assert 0 < s["horizons"]["1-3"]["rank_p"] <= 1
    assert block["selected"] in forecast.MODELS
    assert block["ranking"][0] == block["selected"]
    first = block["fits"]["simplex"]["2023-06-30"]
    assert first["horizons"]["1-3"]["window"] == ["2023-07-01", "2023-09-30"]
    assert first["horizons"]["1-3"]["n_days"] == 92
    assert "weights" not in first["params"]

    drifting, donors = _panel(drift_per_day=-0.0005)  # -1.5% a month, treated only
    block = forecast.rolling_origin(
        "n_trips", drifting, donors, dt.date(2023, 1, 1), ORIGINS, HORIZONS, END, 28, 2.0
    )
    for m in ("simplex", "anchored", "growth"):
        s = block["summary"][m]
        assert not s["passes"], (m, s)
        assert s["horizons"]["4-6"]["bias_pct"] < -2
    assert block["selected"] is None or block["selected"] == "ridge"
