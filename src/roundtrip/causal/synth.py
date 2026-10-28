"""Synthetic control for the CBD (owner design, 2026-10-27).

The treated unit is the CBD aggregate; donors are individual zones outside the CBD.
Because the CBD is 38 zones, its level is far above any single donor, so every series is
scaled by its own mean over the fit window before weights are fitted. Weights are
non-negative and sum to one (fitted by non-negative least squares with a penalised
sum-to-one row), and the forecast is rescaled back to CBD units.

Inference is placebo-in-space: each donor is treated as if it were the treated unit and
the ratio of post- to pre-window RMSPE is compared with the CBD's ratio. The 2023 test
(same calendar window a year earlier, when nothing happened) uses the same code path with
different windows.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from scipy.optimize import nnls

SUM_TO_ONE_PENALTY = 1e4


def fit_weights(y: np.ndarray, x: np.ndarray) -> np.ndarray:
    """Simplex-constrained least squares: w >= 0, sum(w) = 1, min ||y - X w||."""
    t = len(y)
    pen = np.sqrt(SUM_TO_ONE_PENALTY * t)
    x_aug = np.vstack([x, pen * np.ones((1, x.shape[1]))])
    y_aug = np.concatenate([y, [pen]])
    w, _ = nnls(x_aug, y_aug, maxiter=50 * x.shape[1])
    s = w.sum()
    return w / s if s > 0 else np.full(x.shape[1], 1.0 / x.shape[1])


def rmspe(a: pd.Series, b: pd.Series) -> float:
    d = (a - b).to_numpy(dtype=float)
    d = d[~np.isnan(d)]
    return float(np.sqrt(np.mean(d**2))) if len(d) else float("nan")


@dataclass
class SynthFit:
    """One synthetic-control fit and its evaluation over one or more windows."""

    outcome: str
    unit: str
    weights: pd.Series  # donor zone id -> weight
    actual: pd.Series  # day -> actual value
    synthetic: pd.Series  # day -> synthetic value
    fit_start: dt.date
    fit_end: dt.date  # inclusive
    pre_rmspe: float
    windows: dict[str, dict[str, float]] = field(default_factory=dict)
    # name -> {start, end, mean_actual, mean_synthetic, mean_gap, mean_gap_pct, rmspe,
    #          rmspe_ratio}

    def evaluate(self, name: str, start: dt.date, end: dt.date) -> dict[str, float]:
        mask = (self.actual.index >= pd.Timestamp(start)) & (self.actual.index <= pd.Timestamp(end))
        a, s = self.actual[mask], self.synthetic[mask]
        gap = a - s
        res = {
            "n_days": int(mask.sum()),
            "mean_actual": float(a.mean()),
            "mean_synthetic": float(s.mean()),
            "mean_gap": float(gap.mean()),
            "mean_gap_pct": float(100 * gap.mean() / s.mean()) if s.mean() else float("nan"),
            "rmspe": rmspe(a, s),
        }
        res["rmspe_ratio"] = res["rmspe"] / self.pre_rmspe if self.pre_rmspe else float("nan")
        self.windows[name] = res
        return res


def synthetic_control(
    outcome: str,
    treated: pd.Series,
    donors: pd.DataFrame,
    fit_start: dt.date,
    fit_end: dt.date,
    unit: str = "cbd",
) -> SynthFit:
    """Fit weights on [fit_start, fit_end] and return the fitted series over every day.

    ``treated`` is indexed by day; ``donors`` is day x zone. Days where any donor is
    missing are dropped from the fit; days where the treated unit is missing are kept as
    NaN in the output.
    """
    idx = treated.index
    donors = donors.reindex(idx)
    fit_mask = (idx >= pd.Timestamp(fit_start)) & (idx <= pd.Timestamp(fit_end))
    usable = donors.notna().all(axis=1) & treated.notna()
    fit_rows = fit_mask & usable.to_numpy()
    if fit_rows.sum() < 30:
        msg = f"{outcome}: only {int(fit_rows.sum())} usable fit days"
        raise ValueError(msg)

    scale_t = float(treated[fit_rows].mean())
    scale_d = donors[fit_rows].mean(axis=0)
    scale_d = scale_d.where(scale_d != 0, 1.0)
    y = (treated[fit_rows] / scale_t).to_numpy(dtype=float)
    x = (donors[fit_rows] / scale_d).to_numpy(dtype=float)
    w = fit_weights(y, x)

    synthetic = pd.Series((donors / scale_d).to_numpy(dtype=float) @ w * scale_t, index=idx)
    synthetic[~usable.to_numpy() & treated.isna().to_numpy()] = np.nan
    fit = SynthFit(
        outcome=outcome,
        unit=unit,
        weights=pd.Series(w, index=donors.columns, name="weight"),
        actual=treated.astype(float),
        synthetic=synthetic,
        fit_start=fit_start,
        fit_end=fit_end,
        pre_rmspe=rmspe(treated[fit_rows], synthetic[fit_rows]),
    )
    return fit


@dataclass
class PlaceboInSpace:
    """RMSPE-ratio ranks and gap distribution from refitting each donor as treated."""

    window: str
    treated_ratio: float
    treated_gap_pct: float
    donor_ratios: pd.Series
    donor_gaps_pct: pd.Series
    p_value: float  # share of units (incl. treated) with ratio >= treated's
    gap_sd_pct: float  # sd of placebo gaps, for a rough interval on the treated gap

    def interval_pct(self, z: float = 1.96) -> tuple[float, float]:
        return (
            self.treated_gap_pct - z * self.gap_sd_pct,
            self.treated_gap_pct + z * self.gap_sd_pct,
        )

    def to_dict(self) -> dict[str, Any]:
        lo, hi = self.interval_pct()
        return {
            "window": self.window,
            "treated_rmspe_ratio": self.treated_ratio,
            "treated_gap_pct": self.treated_gap_pct,
            "p_value": self.p_value,
            "n_placebos": len(self.donor_ratios),
            "gap_pct_lo": lo,
            "gap_pct_hi": hi,
            "placebo_gaps_pct": {str(k): float(v) for k, v in self.donor_gaps_pct.items()},
        }


def placebo_in_space(
    fit: SynthFit,
    donors: pd.DataFrame,
    window: str,
    start: dt.date,
    end: dt.date,
    max_pre_ratio: float = 5.0,
) -> PlaceboInSpace:
    """Refit with each donor as the treated unit. Donors whose pre-fit is much worse than
    the CBD's (pre-RMSPE more than ``max_pre_ratio`` times) are dropped from the ranking,
    as is standard, because a unit that cannot be matched in the pre-period says nothing
    about the post-period."""
    treated = fit.windows[window] if window in fit.windows else fit.evaluate(window, start, end)
    ratios: dict[int, float] = {}
    gaps: dict[int, float] = {}
    for z in donors.columns:
        others = donors.drop(columns=[z])
        try:
            pf = synthetic_control(
                fit.outcome, donors[z], others, fit.fit_start, fit.fit_end, unit=str(z)
            )
        except ValueError:
            continue
        if fit.pre_rmspe and pf.pre_rmspe > max_pre_ratio * fit.pre_rmspe:
            continue
        ev = pf.evaluate(window, start, end)
        if np.isfinite(ev["rmspe_ratio"]):
            ratios[int(z)] = ev["rmspe_ratio"]
            gaps[int(z)] = ev["mean_gap_pct"]
    r = pd.Series(ratios, dtype=float)
    g = pd.Series(gaps, dtype=float)
    n_ge = int((r >= treated["rmspe_ratio"]).sum())
    return PlaceboInSpace(
        window=window,
        treated_ratio=treated["rmspe_ratio"],
        treated_gap_pct=treated["mean_gap_pct"],
        donor_ratios=r,
        donor_gaps_pct=g,
        p_value=(1 + n_ge) / (1 + len(r)) if len(r) else float("nan"),
        gap_sd_pct=float(g.std(ddof=1)) if len(g) > 1 else float("nan"),
    )
