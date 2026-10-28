"""Figures for the estimates bundle (matplotlib, Agg backend, PNG)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from roundtrip.causal.data import OUTCOME_LABELS
from roundtrip.causal.synth import SynthFit
from roundtrip.config import Settings

COLORS = {"actual": "#1f3a5f", "synthetic": "#d1495b", "band": "#f2c14e"}


def _shade(ax: Any, settings: Settings) -> None:
    ax.axvspan(
        pd.Timestamp(settings.anticipation_start),
        pd.Timestamp(settings.treatment_date),
        color=COLORS["band"],
        alpha=0.25,
        lw=0,
        label="anticipation window",
    )
    ax.axvline(pd.Timestamp(settings.treatment_date), color="black", lw=0.8, ls="--")


def synth_plot(fit: SynthFit, test_fit: SynthFit | None, settings: Settings, path: Path) -> None:
    n = 2 if test_fit is not None else 1
    fig, axes = plt.subplots(n, 1, figsize=(10, 3.4 * n), squeeze=False)
    label = OUTCOME_LABELS.get(fit.outcome, fit.outcome)

    ax = axes[0, 0]
    weekly_a = fit.actual.resample("W").mean()
    weekly_s = fit.synthetic.resample("W").mean()
    ax.plot(weekly_a.index, weekly_a, color=COLORS["actual"], lw=1.4, label="CBD actual")
    ax.plot(weekly_s.index, weekly_s, color=COLORS["synthetic"], lw=1.4, label="synthetic CBD")
    ax.axvline(pd.Timestamp(fit.fit_end), color="grey", lw=0.8, ls=":")
    _shade(ax, settings)
    ax.set_title(f"{label}: CBD vs synthetic control (weekly means; fit to {fit.fit_end})")
    ax.legend(loc="upper left", fontsize=8, frameon=False)
    ax.grid(alpha=0.3)

    if test_fit is not None:
        ax = axes[1, 0]
        s, e = (
            pd.Timestamp(settings.placebo_window_start),
            pd.Timestamp(settings.placebo_window_end),
        )
        lo = s - pd.Timedelta(days=120)
        a = test_fit.actual[lo:e]
        sy = test_fit.synthetic[lo:e]
        ax.plot(a.index, a, color=COLORS["actual"], lw=1.0, label="CBD actual")
        ax.plot(sy.index, sy, color=COLORS["synthetic"], lw=1.0, label="synthetic CBD")
        ax.axvspan(s, e, color=COLORS["band"], alpha=0.25, lw=0, label="2023 test window")
        gap = test_fit.windows.get("placebo_2023", {}).get("mean_gap_pct", float("nan"))
        ax.set_title(f"2023 test: same window a year earlier, mean gap {gap:+.1f}%")
        ax.legend(loc="upper left", fontsize=8, frameon=False)
        ax.grid(alpha=0.3)

    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


OOS_COLORS = {
    "simplex": "#d1495b",
    "ridge": "#6a994e",
    "intercept": "#e0a100",
    "sdid": "#6f4fa0",
    "anchored": "#1b7f79",
}


def oos_plot(fits: dict[str, Any], settings: Settings, path: Path) -> None:
    """Top: weekly CBD actual and each model's forecast, trained on Jan to Nov 2023.
    Bottom: monthly forecast gap, % of forecast, from the end of training."""
    any_fit = next(iter(fits.values()))
    label = OUTCOME_LABELS.get(any_fit.outcome, any_fit.outcome)
    fig, (ax, bx) = plt.subplots(2, 1, figsize=(10, 6.4), sharex=True)
    wa = any_fit.actual.resample("W").mean()
    ax.plot(wa.index, wa, color=COLORS["actual"], lw=1.5, label="CBD actual")
    for name, f in fits.items():
        wp = f.predicted.resample("W").mean()
        ax.plot(
            wp.index, wp, color=OOS_COLORS.get(name, "grey"), lw=1.1, label=name.replace("_", " ")
        )
        mg = f.monthly_gap_pct(settings.oos_train_end)
        bx.plot(
            mg.index,
            mg,
            color=OOS_COLORS.get(name, "grey"),
            lw=1.3,
            marker="o",
            ms=3,
            label=name.replace("_", " "),
        )
    for a in (ax, bx):
        a.axvspan(
            pd.Timestamp(settings.oos_train_start),
            pd.Timestamp(settings.oos_train_end),
            color="grey",
            alpha=0.12,
            lw=0,
        )
        a.axvspan(
            pd.Timestamp(settings.oos_train_end),
            pd.Timestamp(settings.oos_holdout_end),
            color="#2e86ab",
            alpha=0.07,
            lw=0,
        )
        _shade(a, settings)
        a.grid(alpha=0.3)
    tol = settings.oos_drift_tolerance_pct
    bx.axhspan(-tol, tol, color="black", alpha=0.06, lw=0)
    bx.axhline(0, color="black", lw=0.6)
    ax.set_title(
        f"{label}: forecasts trained on Jan to 13 Nov 2023 only (grey), "
        "untreated hold-out year (blue)",
        fontsize=10,
    )
    bx.set_title(f"Monthly forecast gap, actual vs forecast, % (band = +/-{tol:g}%)", fontsize=10)
    ax.legend(loc="upper left", fontsize=8, frameon=False, ncol=2)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def event_study_plot(
    results: dict[str, dict[str, Any]], group: str, settings: Settings, path: Path
) -> None:
    outcomes = list(results)
    n = len(outcomes)
    cols = 2
    rows = (n + cols - 1) // cols
    fig, axes = plt.subplots(rows, cols, figsize=(11, 2.8 * rows), squeeze=False)
    for i, outcome in enumerate(outcomes):
        ax = axes[i // cols, i % cols]
        r = results[outcome]
        weeks = [d["week"] for d in r["dynamic"]]
        est = [d["estimate"] for d in r["dynamic"]]
        lo = [d["lo"] for d in r["dynamic"]]
        hi = [d["hi"] for d in r["dynamic"]]
        ax.fill_between(weeks, lo, hi, color=COLORS["actual"], alpha=0.15, lw=0)
        ax.plot(weeks, est, color=COLORS["actual"], lw=1.2, marker="o", ms=2.5)
        ax.axhline(0, color="black", lw=0.6)
        ax.axvline(-0.5, color="black", lw=0.8, ls="--")
        ax.axvspan(
            r.get("anticipation_week", -8) - 0.5, -0.5, color=COLORS["band"], alpha=0.25, lw=0
        )
        unit = "log points" if r["transform"] == "log" else "level"
        label = OUTCOME_LABELS.get(outcome, outcome)
        title = f"{label} ({unit}); pre-trend p={r['pretrend_p']:.2f}"
        ax.set_title(title, fontsize=9)
        ax.set_xlabel(f"weeks from {settings.treatment_date:%d %b %Y}", fontsize=8)
        ax.grid(alpha=0.3)
    for j in range(n, rows * cols):
        axes[j // cols, j % cols].axis("off")
    fig.suptitle(
        f"Event study, CBD vs {group.replace('_', ' ')} "
        "(zone and week fixed effects, 95% CI clustered by zone)",
        fontsize=10,
    )
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def comparison_plot(estimates: list[dict[str, Any]], path: Path) -> None:
    df = pd.DataFrame(estimates)
    df = df[(df["window"] == "post") & df["adjusted"]]
    if df.empty:
        df = pd.DataFrame(estimates)
        df = df[df["adjusted"]]
    outcomes = list(dict.fromkeys(df["outcome"]))
    fig, ax = plt.subplots(figsize=(9, 0.55 * len(df) + 1.5))
    y = 0.0
    ticks, labels = [], []
    for outcome in outcomes:
        sub = df[df["outcome"] == outcome]
        for _, r in sub.iterrows():
            ax.errorbar(
                r["effect_pct"],
                y,
                xerr=[[r["effect_pct"] - r["lo_pct"]], [r["hi_pct"] - r["effect_pct"]]],
                fmt="o",
                color=COLORS["actual"],
                capsize=3,
                ms=4,
            )
            ticks.append(y)
            labels.append(
                f"{OUTCOME_LABELS.get(outcome, outcome)} · {r['design'].replace('_', ' ')}"
            )
            y += 1
        y += 0.6
    ax.axvline(0, color="black", lw=0.8)
    ax.set_yticks(ticks)
    ax.set_yticklabels(labels, fontsize=8)
    ax.invert_yaxis()
    ax.set_xlabel("effect on the CBD, % of counterfactual (95% interval)", fontsize=9)
    ax.set_title(
        "Post-5 Jan 2025 effects under the three comparisons (donor-adjusted)", fontsize=10
    )
    ax.grid(axis="x", alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)
