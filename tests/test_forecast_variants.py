"""The intercept and SDID forecast variants and the change-point test."""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from roundtrip.causal import forecast


def _panel(seed: int = 0, step_at: int | None = None) -> tuple[pd.Series, pd.DataFrame]:
    """Treated unit as a noisy combination of 6 donors over 500 days, with an
    optional level step in the treated unit at day ``step_at``."""
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
    return pd.Series(treated, index=idx), donors


def test_intercept_and_sdid_forecast_a_stable_unit() -> None:
    treated, donors = _panel()
    train_end = dt.date(2023, 9, 30)
    for model in ("intercept", "sdid"):
        f = forecast.fit_forecast(
            model,
            "n_trips",
            treated,
            donors,
            dt.date(2023, 1, 1),
            train_end,
            target=(dt.date(2023, 10, 1), dt.date(2024, 5, 14)),
        )
        w = f.window(dt.date(2023, 10, 1), dt.date(2024, 5, 14))
        assert abs(w["mean_gap_pct"]) < 2, (model, w)
        assert "intercept_pct" in f.params
    sd = forecast.fit_forecast(
        "sdid", "n_trips", treated, donors, dt.date(2023, 1, 1), train_end
    ).params
    assert 1 < sd["lambda_effective_days"] <= 273
    assert sd["lambda_mean_day"].startswith("2023")


def test_anchored_carries_a_late_training_level_shift_forward() -> None:
    # a 5% drop in the treated unit four months before training ends: the simplex averages it
    # into its weights; the anchored design carries the late level forward. SDID's time
    # weights spread over the seasonal training window, so it stays close to the simplex.
    treated, donors = _panel(step_at=150)
    train_end = dt.date(2023, 9, 30)
    target = (dt.date(2023, 10, 1), dt.date(2024, 5, 14))
    gaps = {}
    for model in ("simplex", "sdid", "anchored"):
        f = forecast.fit_forecast(
            model, "n_trips", treated, donors, dt.date(2023, 1, 1), train_end, target=target
        )
        gaps[model] = f.window(*target)["mean_gap_pct"]
    assert gaps["simplex"] < -1.5
    assert abs(gaps["anchored"]) < 1.5
    assert abs(gaps["sdid"] - gaps["simplex"]) < 1.5


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
            for m, g in (("simplex", -5.0), ("ridge", -4.0), ("sdid", 0.5))
        }
    }
    v = forecast.drift_verdict(block, 3.0)
    assert v["sdid"]["passes"] and not v["sdid"]["counts"]
    assert v["simplex"]["counts"] and not v["simplex"]["passes"]
    assert not forecast.verdict_passes(v)
