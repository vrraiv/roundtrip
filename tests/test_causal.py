"""Causal module on a simulated panel with a known treatment effect (brief section 8)."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from roundtrip.causal import comparisons
from roundtrip.causal.data import (
    add_outcomes,
    group_series,
    zone_groups,
    zone_series,
    zone_week_panel,
)
from roundtrip.causal.event_study import event_study, placebo_dates
from roundtrip.causal.run import run_causal
from roundtrip.causal.synth import fit_weights, placebo_in_space, synthetic_control
from roundtrip.config import Settings

TRUE_TRIP_EFFECT = -0.15  # CBD trips fall 15% after 5 Jan 2025
TRUE_WAIT_EFFECT = 0.10  # CBD waits rise 10%
N_CBD, N_NORTH, N_INNER = 8, 14, 10


def simulate(settings: Settings, seed: int = 7) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Zone x day sufficient statistics with a common seasonal factor, zone levels, and
    a treatment effect on CBD trips and waits from the treatment date."""
    rng = np.random.default_rng(seed)
    days = pd.date_range("2023-01-01", "2025-06-30", freq="D")
    zones = list(range(1, N_CBD + N_NORTH + N_INNER + 1))
    cbd = zones[:N_CBD]
    north = zones[N_CBD : N_CBD + N_NORTH]
    inner = zones[N_CBD + N_NORTH :]
    t = np.arange(len(days))
    season = 1 + 0.15 * np.sin(2 * np.pi * t / 365.25) + 0.08 * np.sin(2 * np.pi * t / 7)
    trend = 1 + 0.0002 * t
    common = season * trend
    rows = []
    post = days >= pd.Timestamp(settings.treatment_date)
    for z in zones:
        level = rng.uniform(800, 3000)
        wait = rng.uniform(250, 400)
        fare = rng.uniform(18, 32)
        zone_noise = rng.normal(1, 0.04, size=len(days))
        n = level * common * zone_noise
        w = wait * (1 + 0.1 * np.sin(2 * np.pi * t / 365.25)) * rng.normal(1, 0.03, size=len(days))
        f = fare * (1 + 0.05 * np.sin(2 * np.pi * t / 365.25)) * rng.normal(1, 0.02, size=len(days))
        if z in cbd:
            n = n * np.where(post, 1 + TRUE_TRIP_EFFECT, 1)
            w = w * np.where(post, 1 + TRUE_WAIT_EFFECT, 1)
        n = np.round(n).astype(int)
        rows.append(
            pd.DataFrame(
                {
                    "zone_id": z,
                    "day": days,
                    "n_trips": n,
                    "n_airport_trips": 0,
                    "n_shared_request": (0.1 * n).astype(int),
                    "n_wait_valid": n,
                    "wait_sum": w * n,
                    "fare_sum": f * n,
                    "pay_sum": 0.7 * f * n,
                    "tips_sum": 0.05 * f * n,
                    "cbd_fee_sum": 0.0,
                }
            )
        )
    day = pd.concat(rows, ignore_index=True)
    borough = {z: "Manhattan" for z in cbd + north} | {z: "Brooklyn" for z in inner}
    lookup = pd.DataFrame(
        {
            "zone_id": zones,
            "borough": [borough[z] for z in zones],
            "zone_name": [f"Z{z}" for z in zones],
        }
    )
    return day, lookup


@pytest.fixture
def sim_settings(settings: Settings) -> Settings:
    zones = list(range(1, N_CBD + N_NORTH + N_INNER + 1))
    return settings.model_copy(
        update={
            "cbd_zones": tuple(zones[:N_CBD]),
            "inner_bk_queens_zones": tuple(zones[N_CBD + N_NORTH :]),
            "manhattan_excluded_zones": (),
            "airport_zones": (),
            "bootstrap_reps": 100,
        }
    )


def test_fit_weights_is_simplex() -> None:
    rng = np.random.default_rng(0)
    x = rng.normal(size=(100, 5))
    true_w = np.array([0.5, 0.3, 0.2, 0, 0])
    y = x @ true_w + rng.normal(scale=0.01, size=100)
    w = fit_weights(y, x)
    assert w.min() >= 0
    assert abs(w.sum() - 1) < 1e-6
    assert np.allclose(w, true_w, atol=0.03)


def test_synthetic_control_recovers_effect(sim_settings: Settings) -> None:
    day, lookup = simulate(sim_settings)
    groups = zone_groups(sim_settings, lookup)
    cbd = group_series(day, groups.cbd).set_index("day")
    donors = zone_series(day, "n_trips", groups.donors)
    fit_end = sim_settings.anticipation_start - dt.timedelta(days=1)
    fit = synthetic_control("n_trips", cbd["n_trips"], donors, dt.date(2023, 1, 1), fit_end)
    post = fit.evaluate("post", sim_settings.treatment_date, dt.date(2025, 6, 30))
    ant = fit.evaluate(
        "anticipation", sim_settings.anticipation_start, fit_end + dt.timedelta(days=52)
    )
    assert abs(post["mean_gap_pct"] - 100 * TRUE_TRIP_EFFECT) < 3
    assert abs(ant["mean_gap_pct"]) < 3
    assert fit.pre_rmspe < 0.02 * cbd["n_trips"].mean()
    plac = placebo_in_space(fit, donors, "post", sim_settings.treatment_date, dt.date(2025, 6, 30))
    assert plac.p_value < 0.1
    lo, hi = plac.interval_pct()
    assert lo < 100 * TRUE_TRIP_EFFECT < hi


def test_event_study_recovers_effect(sim_settings: Settings) -> None:
    day, lookup = simulate(sim_settings)
    groups = zone_groups(sim_settings, lookup)
    weeks = zone_week_panel(day, sim_settings)
    assert weeks.groupby("week")["zone_id"].count().min() == len(groups.cbd) + len(groups.donors)
    r = event_study(
        weeks, "n_trips", groups.cbd, groups.manhattan_north, sim_settings, "manhattan_north"
    )
    assert abs(r.static["post"].estimate - np.log(1 + TRUE_TRIP_EFFECT)) < 0.03
    assert r.static["post"].lo < np.log(1 + TRUE_TRIP_EFFECT) < r.static["post"].hi
    assert abs(r.static["anticipation"].estimate) < 0.03
    assert np.isfinite(r.pretrend_p)
    post_dyn = [c.estimate for c, w in zip(r.dynamic, r.dynamic_weeks, strict=True) if w >= 0]
    assert abs(np.mean(post_dyn) - np.log(1 + TRUE_TRIP_EFFECT)) < 0.03
    rw = event_study(
        weeks,
        "mean_wait_seconds",
        groups.cbd,
        groups.inner_bk_queens,
        sim_settings,
        "inner_bk_queens",
    )
    cbd_wait = add_outcomes(group_series(day[day["day"] < "2024-11-14"], groups.cbd))[
        "mean_wait_seconds"
    ].mean()
    assert abs(rw.static["post"].estimate / cbd_wait - TRUE_WAIT_EFFECT) < 0.03
    fakes = placebo_dates(
        weeks, "n_trips", groups.cbd, groups.manhattan_north, sim_settings, "manhattan_north"
    )
    assert len(fakes) == len(sim_settings.placebo_dates)
    assert all(abs(f["estimate"]) < 0.03 for f in fakes)


def test_comparisons_recover_effect(sim_settings: Settings) -> None:
    day, lookup = simulate(sim_settings)
    groups = zone_groups(sim_settings, lookup)
    cbd = group_series(day, groups.cbd).set_index("day")
    donors = group_series(day, groups.donors).set_index("day")
    s, e = sim_settings.treatment_date, dt.date(2025, 6, 30)
    raw, adj = comparisons.prior_year(
        cbd["n_trips"], donors["n_trips"], "n_trips", "post", s, e, sim_settings
    )
    assert abs(adj.effect_pct - 100 * TRUE_TRIP_EFFECT) < 4
    # coverage is probabilistic; allow one point of slack on either side
    assert adj.lo_pct - 1 < 100 * TRUE_TRIP_EFFECT < adj.hi_pct + 1
    assert adj.hi_pct - adj.lo_pct < 5
    assert raw.counterfactual > 0
    _raw2, adj2 = comparisons.pre_window(
        cbd["n_trips"], donors["n_trips"], "n_trips", "post", s, e, sim_settings
    )
    assert abs(adj2.effect_pct - 100 * TRUE_TRIP_EFFECT) < 4
    assert adj2.lo_pct - 1 < 100 * TRUE_TRIP_EFFECT < adj2.hi_pct + 1
    assert adj2.hi_pct - adj2.lo_pct < 8


def write_sim_panel(day: pd.DataFrame, lookup: pd.DataFrame, settings: Settings) -> None:
    """Write the simulated daily statistics in the zone-hour panel layout (one row per
    zone-day at noon) so ``run_causal`` can read them like real output."""
    d = add_outcomes(day)
    d["hour_ts"] = d["day"] + pd.Timedelta(hours=12)
    d["month"] = d["day"].dt.strftime("%Y-%m")
    d["pu_zone"] = d["zone_id"]
    d["base"] = "Uber"
    d["total_base_fare"] = d["fare_sum"]
    d["total_driver_pay"] = d["pay_sum"]
    d["mean_tips"] = d["tips_sum"] / d["n_trips"]
    d["mean_cbd_congestion_fee"] = 0.0
    cols = [
        "month",
        "pu_zone",
        "hour_ts",
        "base",
        "n_trips",
        "n_airport_trips",
        "n_shared_request",
        "n_wait_valid",
        "mean_wait_seconds",
        "total_base_fare",
        "total_driver_pay",
        "mean_tips",
        "mean_cbd_congestion_fee",
    ]
    for month, sub in d.groupby("month"):
        p = settings.panel_path(str(month))
        p.parent.mkdir(parents=True, exist_ok=True)
        sub[cols].to_parquet(p, index=False)
    settings.zone_lookup_path.parent.mkdir(parents=True, exist_ok=True)
    lk = lookup.rename(columns={"zone_id": "LocationID", "borough": "Borough", "zone_name": "Zone"})
    lk["service_zone"] = "Boro Zone"
    lk.to_csv(settings.zone_lookup_path, index=False)


def test_run_causal_end_to_end(sim_settings: Settings, tmp_path: Path) -> None:
    day, lookup = simulate(sim_settings)
    write_sim_panel(day, lookup, sim_settings)
    out = run_causal(sim_settings, out_dir=tmp_path / "est", quick=True)
    bundle = json.loads((out / "estimates.json").read_text(encoding="utf-8"))
    assert bundle["data_end"] == "2025-06-30"
    assert set(bundle["windows"]) == {"anticipation", "post"}
    head = bundle["headline"]["n_trips"]
    assert head["design"] == "synthetic_control"
    assert abs(head["effect_pct"] - 100 * TRUE_TRIP_EFFECT) < 3
    assert bundle["synth"]["n_trips"]["test_2023"]["windows"]["placebo_2023"][
        "mean_gap_pct"
    ] == pytest.approx(0, abs=3)
    assert {e["design"] for e in bundle["estimates"]} == {
        "prior_year",
        "pre_window",
        "synthetic_control",
    }
    assert set(bundle["event_study"]) == {"manhattan_north", "inner_bk_queens"}
    assert any(p["kind"] == "fake_zones" for p in bundle["placebos"])
    assert (out / "figures" / "synth_n_trips.png").exists()
    assert (out / "figures" / "event_study_manhattan_north.png").exists()
    assert (out / "figures" / "comparisons.png").exists()
    assert set(bundle["synth_oos"]) == set(sim_settings.synth_outcomes)
    bt = bundle["break_tests"]["n_trips"]
    assert "sup_f" in bt["share"] and bt["placebo"]["n"] > 0
    assert set(bt["oos_gap"]) == set(forecast_models())
    sv = bundle["synth_variants"]["n_trips"]["models"]
    assert set(sv) == set(forecast_models())
    for m, r in sv.items():
        assert abs(r["windows"]["post"]["mean_gap_pct"] - 100 * TRUE_TRIP_EFFECT) < 4, m
    sr = bundle["synth_rolling"]["n_trips"]
    assert set(sr["summary"]) == set(forecast_models())
    assert set(sr["summary_after"]) == set(forecast_models())
    assert sr["after_origin"] == sim_settings.rolling_break_week.isoformat()
    assert sr["summary"]["simplex"]["passes"] and sr["selected"] is not None
    assert sr["origins"] == [d.isoformat() for d in sim_settings.rolling_origins]
    assert (out / "figures" / "synth_rolling_n_trips.png").exists()
    py = [e for e in bundle["prior_year_placebo"] if e["outcome"] == "n_trips" and e["adjusted"]]
    assert abs(py[0]["effect_pct"]) < 2
    assert (out / "figures" / "synth_oos_n_trips.png").exists()


def forecast_models() -> tuple[str, ...]:
    from roundtrip.causal import forecast

    return forecast.MODELS


def _oos_windows(s: Settings) -> dict[str, tuple[dt.date, dt.date]]:
    return {
        "holdout": (s.oos_train_end + dt.timedelta(days=1), s.oos_holdout_end),
        "holdout_late": (s.oos_holdout_end - dt.timedelta(days=90), s.oos_holdout_end),
        "post": (s.treatment_date, dt.date(2025, 6, 30)),
    }


def test_oos_forecast_passes_on_stable_panel(sim_settings: Settings) -> None:
    from roundtrip.causal import forecast

    day, lookup = simulate(sim_settings)
    groups = zone_groups(sim_settings, lookup)
    cbd = group_series(day, groups.cbd).set_index("day")
    donors = zone_series(day, "n_trips", groups.donors)
    block, fits = forecast.evaluate(
        "n_trips",
        cbd["n_trips"],
        donors,
        sim_settings.oos_train_start,
        sim_settings.oos_train_end,
        _oos_windows(sim_settings),
    )
    verdict = forecast.drift_verdict(block, sim_settings.oos_drift_tolerance_pct)
    assert verdict["simplex"]["passes"]
    for m in forecast.MODELS:
        r = block["models"][m]
        assert abs(r["windows"]["holdout"]["mean_gap_pct"]) < 3
        assert abs(r["windows"]["post"]["mean_gap_pct"] - 100 * TRUE_TRIP_EFFECT) < 4
    assert set(fits) == set(forecast.MODELS)
    assert "alpha" in block["models"]["ridge"]["params"]


def test_oos_forecast_detects_drift(sim_settings: Settings) -> None:
    from roundtrip.causal import forecast

    day, lookup = simulate(sim_settings)
    groups = zone_groups(sim_settings, lookup)
    # CBD-only decline of 1% a month from December 2023: drift no donor shares
    months = ((day["day"] - pd.Timestamp("2023-12-01")).dt.days / 30.4).clip(lower=0)
    drift = np.where(day["zone_id"].isin(groups.cbd), 1 - 0.01 * months, 1.0)
    for col in ("n_trips", "n_wait_valid", "wait_sum", "fare_sum", "pay_sum", "tips_sum"):
        day[col] = day[col] * drift
    cbd = group_series(day, groups.cbd).set_index("day")
    donors = zone_series(day, "n_trips", groups.donors)
    block, _ = forecast.evaluate(
        "n_trips",
        cbd["n_trips"],
        donors,
        sim_settings.oos_train_start,
        sim_settings.oos_train_end,
        _oos_windows(sim_settings),
    )
    verdict = forecast.drift_verdict(block, sim_settings.oos_drift_tolerance_pct)
    assert not verdict["simplex"]["passes"]
    assert verdict["simplex"]["late_holdout_gap_pct"] < -7
    tr = block["models"]["simplex"]["trend"]
    assert tr["slope_pct_per_month"] < -0.5
    assert tr["extrapolated"]["post"] < verdict["simplex"]["late_holdout_gap_pct"]
    assert block["models"]["simplex"]["placebo"]["rank_p"] < 0.2
