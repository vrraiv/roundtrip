"""Run every phase 2 analysis and write one estimates bundle plus figures.

Output: ``data/derived/estimates/<run_id>/estimates.json`` and ``figures/*.png``. The
bundle is what the report builder and the review dashboard read; it is also what gets
copied into ``releases/<month>/`` on approval, so it must be self-contained.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import time
from pathlib import Path
from typing import Any

import pandas as pd

from roundtrip import __version__
from roundtrip.causal import comparisons, figures, forecast
from roundtrip.causal.data import (
    OUTCOMES,
    ZoneGroups,
    complete_days,
    group_series,
    load_zone_day,
    load_zones,
    zone_groups,
    zone_series,
    zone_week_panel,
)
from roundtrip.causal.event_study import (
    ES_OUTCOMES,
    event_study,
    placebo_dates,
    placebo_zones,
)
from roundtrip.causal.synth import PlaceboInSpace, SynthFit, placebo_in_space, synthetic_control
from roundtrip.config import Settings
from roundtrip.db import connect
from roundtrip.ingest.manifest import Manifest
from roundtrip.pipeline import git_sha

log = logging.getLogger(__name__)

HEADLINE_OUTCOMES: tuple[str, ...] = ("n_trips", "mean_wait_seconds", "mean_base_fare")
PLACEBO_ZONE_OUTCOMES: tuple[str, ...] = HEADLINE_OUTCOMES
MAX_DONOR_MISSING_SHARE = 0.02


def _windows(settings: Settings, data_end: dt.date) -> dict[str, tuple[dt.date, dt.date]]:
    out: dict[str, tuple[dt.date, dt.date]] = {}
    ant_end = settings.treatment_date - dt.timedelta(days=1)
    if data_end >= settings.anticipation_start:
        out["anticipation"] = (settings.anticipation_start, min(ant_end, data_end))
    if data_end >= settings.treatment_date:
        post_end = settings.treatment_date + dt.timedelta(days=settings.post_window_days - 1)
        out["post"] = (settings.treatment_date, min(post_end, data_end))
    return out


def _usable_donors(wide: pd.DataFrame, start: dt.date, end: dt.date) -> pd.DataFrame:
    mask = (wide.index >= pd.Timestamp(start)) & (wide.index <= pd.Timestamp(end))
    missing = wide[mask].isna().mean()
    keep = missing[missing <= MAX_DONOR_MISSING_SHARE].index
    return wide[keep]


def _synth_block(
    fit: SynthFit, placebos: dict[str, PlaceboInSpace], groups: ZoneGroups
) -> dict[str, Any]:
    top = fit.weights.sort_values(ascending=False)
    top = top[top > 0.005]
    return {
        "fit_start": fit.fit_start.isoformat(),
        "fit_end": fit.fit_end.isoformat(),
        "pre_rmspe": fit.pre_rmspe,
        "n_donors": len(fit.weights),
        "weights": {
            f"{z} {groups.label(z)}": float(w)
            for z, w in zip(top.index.astype(int).tolist(), top.to_numpy(), strict=True)
        },
        "windows": fit.windows,
        "placebo": {k: v.to_dict() for k, v in placebos.items()},
        "series": {
            "day": [d.strftime("%Y-%m-%d") for d in fit.actual.index],
            "actual": [None if pd.isna(v) else float(v) for v in fit.actual],
            "synthetic": [None if pd.isna(v) else float(v) for v in fit.synthetic],
        },
    }


def run_causal(settings: Settings, out_dir: Path | None = None, quick: bool = False) -> Path:
    """Run synthetic control, the three comparisons, the event studies and placebos.

    ``quick`` reduces placebo draws and bootstrap reps for tests.
    """
    t0 = time.perf_counter()
    if quick:
        settings = settings.model_copy(update={"bootstrap_reps": 50})
    con = connect(settings)
    zones = load_zones(settings, con)
    groups = zone_groups(settings, zones)
    day = load_zone_day(settings, con)
    if day.empty:
        msg = f"no panel data under {settings.panel_glob}"
        raise FileNotFoundError(msg)
    days = complete_days(day)
    data_start, data_end = days.min().date(), days.max().date()
    windows = _windows(settings, data_end)
    log.info(
        "panel: %s to %s, %d zones, windows %s",
        data_start,
        data_end,
        day["zone_id"].nunique(),
        list(windows),
    )

    run_id = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    out = out_dir or settings.estimates_dir / run_id
    fig_dir = out / "figures"
    fig_dir.mkdir(parents=True, exist_ok=True)

    cbd = group_series(day, groups.cbd).set_index("day")
    donors_agg = group_series(day, groups.donors).set_index("day")

    # --- synthetic control ---------------------------------------------------------
    synth: dict[str, Any] = {}
    synth_fits: dict[str, tuple[SynthFit, dict[str, PlaceboInSpace]]] = {}
    fit_end = settings.anticipation_start - dt.timedelta(days=1)
    test_fit_end = settings.placebo_window_start - dt.timedelta(days=1)
    test_end = settings.placebo_window_end - dt.timedelta(days=1)
    for outcome in settings.synth_outcomes:
        wide = _usable_donors(zone_series(day, outcome, groups.donors), data_start, data_end)
        treated = cbd[outcome]
        fit = synthetic_control(outcome, treated, wide, data_start, fit_end)
        placebos: dict[str, PlaceboInSpace] = {}
        for name, (s, e) in windows.items():
            fit.evaluate(name, s, e)
            placebos[name] = placebo_in_space(fit, wide, name, s, e)
        synth_fits[outcome] = (fit, placebos)
        block = _synth_block(fit, placebos, groups)
        # The 2023 test: same window one year earlier, nothing happened.
        if data_end >= test_end and test_fit_end > data_start + dt.timedelta(days=60):
            tfit = synthetic_control(outcome, treated, wide, data_start, test_fit_end)
            tfit.evaluate("placebo_2023", settings.placebo_window_start, test_end)
            tplac = {
                "placebo_2023": placebo_in_space(
                    tfit, wide, "placebo_2023", settings.placebo_window_start, test_end
                )
            }
            block["test_2023"] = _synth_block(tfit, tplac, groups)
            figures.synth_plot(fit, tfit, settings, fig_dir / f"synth_{outcome}.png")
        else:
            figures.synth_plot(fit, None, settings, fig_dir / f"synth_{outcome}.png")
        synth[outcome] = block
        log.info(
            "synth %s: pre-RMSPE %.3f, windows %s",
            outcome,
            fit.pre_rmspe,
            {k: round(v["mean_gap_pct"], 2) for k, v in fit.windows.items()},
        )

    # --- out-of-sample test of the synthetic control -----------------------------------
    synth_oos: dict[str, Any] = {}
    hold_start = settings.oos_train_end + dt.timedelta(days=1)
    if data_end >= settings.oos_holdout_end:
        oos_windows = {
            "holdout": (hold_start, settings.oos_holdout_end),
            "holdout_late": (
                settings.oos_holdout_end - dt.timedelta(days=90),
                settings.oos_holdout_end,
            ),
            **windows,
        }
        for outcome in settings.synth_outcomes:
            wide = _usable_donors(zone_series(day, outcome, groups.donors), data_start, data_end)
            block, fits = forecast.evaluate(
                outcome,
                cbd[outcome],
                wide,
                settings.oos_train_start,
                settings.oos_train_end,
                oos_windows,
            )
            block["verdict"] = forecast.drift_verdict(block, settings.oos_drift_tolerance_pct)
            for res in block["models"].values():
                w = res["params"].get("weights")
                if w:
                    res["params"]["weights"] = {
                        f"{z} {groups.label(int(z))}": v
                        for z, v in sorted(w.items(), key=lambda kv: -kv[1])[:6]
                    }
            synth_oos[outcome] = block
            figures.oos_plot(fits, settings, fig_dir / f"synth_oos_{outcome}.png")
            log.info(
                "synth OOS %s: hold-out gap %s",
                outcome,
                {
                    m: round(r["windows"]["holdout"]["mean_gap_pct"], 2)
                    for m, r in block["models"].items()
                },
            )

    # --- three comparisons -----------------------------------------------------------
    estimates: list[dict[str, Any]] = []
    for outcome in OUTCOMES:
        if outcome == "mean_cbd_fee":
            continue
        for name, (s, e) in windows.items():
            for est in comparisons.prior_year(
                cbd[outcome], donors_agg[outcome], outcome, name, s, e, settings
            ):
                estimates.append(est.to_dict())
            for est in comparisons.pre_window(
                cbd[outcome], donors_agg[outcome], outcome, name, s, e, settings
            ):
                estimates.append(est.to_dict())
            if outcome in synth_fits:
                fit, placebos = synth_fits[outcome]
                estimates.append(
                    comparisons.from_synth(fit, placebos[name], outcome, name).to_dict()
                )
    figures.comparison_plot(estimates, fig_dir / "comparisons.png")

    # Prior-year design on an untreated window: any "effect" here is drift, not policy.
    prior_year_placebo: list[dict[str, Any]] = []
    py_end = settings.anticipation_start - dt.timedelta(days=1)
    if data_start <= settings.prior_year_placebo_start - dt.timedelta(days=364):
        for outcome in OUTCOMES:
            if outcome == "mean_cbd_fee":
                continue
            for est in comparisons.prior_year(
                cbd[outcome],
                donors_agg[outcome],
                outcome,
                "untreated_2024",
                settings.prior_year_placebo_start,
                py_end,
                settings,
            ):
                prior_year_placebo.append(est.to_dict())

    # --- event studies -----------------------------------------------------------------
    weeks = zone_week_panel(day, settings)
    es: dict[str, dict[str, Any]] = {}
    placebo_rows: list[dict[str, Any]] = []
    for gname, gzones in groups.controls.items():
        es[gname] = {}
        for outcome in ES_OUTCOMES:
            r = event_study(weeks, outcome, groups.cbd, gzones, settings, gname)
            es[gname][outcome] = r.to_dict()
            placebo_rows += placebo_dates(weeks, outcome, groups.cbd, gzones, settings, gname)
            if outcome in PLACEBO_ZONE_OUTCOMES:
                placebo_rows += placebo_zones(
                    weeks,
                    outcome,
                    gzones,
                    settings,
                    gname,
                    n_draws=5 if quick else 20,
                    seed=settings.bootstrap_seed,
                )
            log.info(
                "event study %s/%s: post %.3f (%.3f, %.3f), pretrend p=%.2f",
                gname,
                outcome,
                r.static["post"].estimate,
                r.static["post"].lo,
                r.static["post"].hi,
                r.pretrend_p,
            )
        figures.event_study_plot(es[gname], gname, settings, fig_dir / f"event_study_{gname}.png")

    # --- headline ----------------------------------------------------------------------
    headline = {}
    for outcome in HEADLINE_OUTCOMES:
        rows = [
            e
            for e in estimates
            if e["outcome"] == outcome
            and e["window"] == "post"
            and e["design"] == "synthetic_control"
        ]
        if rows:
            headline[outcome] = rows[0]

    manifest = Manifest.load(settings.manifest_path) if settings.manifest_path.exists() else None
    bundle = {
        "run_id": run_id,
        "generated_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "package_version": __version__,
        "git_sha": git_sha(),
        "manifest_hash": manifest.content_hash() if manifest else None,
        "data_start": data_start.isoformat(),
        "data_end": data_end.isoformat(),
        "n_days": len(days),
        "months": sorted({d.strftime("%Y-%m") for d in days}),
        "design": {
            "treatment_date": settings.treatment_date.isoformat(),
            "anticipation_start": settings.anticipation_start.isoformat(),
            "placebo_window": [
                settings.placebo_window_start.isoformat(),
                settings.placebo_window_end.isoformat(),
            ],
            "pre_window_start": settings.pre_window_start.isoformat(),
            "exclude_airport_trips": settings.exclude_airport_trips,
            "comparison_designs": list(settings.comparison_designs),
            "synth_outcomes": list(settings.synth_outcomes),
            "placebo_dates": [d.isoformat() for d in settings.placebo_dates],
            "event_weeks": [settings.event_weeks_before, settings.event_weeks_after],
        },
        "groups": {
            "cbd": groups.cbd,
            "manhattan_north": groups.manhattan_north,
            "inner_bk_queens": groups.inner_bk_queens,
            "n_donors": len(groups.donors),
        },
        "windows": {
            k: {"start": s.isoformat(), "end": e.isoformat()} for k, (s, e) in windows.items()
        },
        "headline": headline,
        "estimates": estimates,
        "synth": synth,
        "synth_oos": synth_oos,
        "prior_year_placebo": prior_year_placebo,
        "event_study": es,
        "placebos": placebo_rows,
        "elapsed_seconds": round(time.perf_counter() - t0, 1),
    }
    (out / "estimates.json").write_text(json.dumps(bundle, indent=1, default=str), encoding="utf-8")
    log.info("wrote %s in %.0fs", out / "estimates.json", bundle["elapsed_seconds"])
    return out
