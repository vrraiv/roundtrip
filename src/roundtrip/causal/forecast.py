"""Out-of-sample evaluation of the synthetic control (owner review notes, 2026-08 release).

The reviewer asked whether the synthetic control is a credible counterfactual or only
fits well in-sample. The test: train each counterfactual model on 1 January to
13 November 2023 only, then forecast forward with the weights frozen. The year from
14 November 2023 to 13 November 2024 is untreated, so a credible model's forecast should
stay on the actual CBD series there. Any systematic gap in that year is drift, and the
same drift contaminates the anticipation and post estimates.

Two models, both trained on the same window and both using only same-day donor values:

- ``simplex``: the current design. Non-negative weights summing to one on mean-scaled
  series.
- ``ridge``: unconstrained linear regression of the CBD on all donors with an intercept
  and a ridge penalty. The penalty is chosen on the last eight weeks of the training
  window, never on held-out data.

A simplex-with-intercept variant (Doudchenko and Imbens 2016) was tried and dropped: on
mean-scaled series every series already has training mean one, so the intercept is zero
and the weights are identical to ``simplex``.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from roundtrip.causal.synth import fit_weights

MODELS: tuple[str, ...] = ("simplex", "ridge")
RIDGE_ALPHAS: tuple[float, ...] = (0.01, 0.1, 1.0, 10.0, 100.0, 1000.0)
VALIDATION_DAYS = 56


def _ridge(y: np.ndarray, x: np.ndarray, alpha: float) -> tuple[float, np.ndarray]:
    """Ridge with an unpenalised intercept on standardised columns."""
    mu_x, sd_x = x.mean(axis=0), x.std(axis=0)
    sd_x = np.where(sd_x > 0, sd_x, 1.0)
    z = (x - mu_x) / sd_x
    mu_y = y.mean()
    k = z.shape[1]
    beta_z = np.linalg.solve(z.T @ z + alpha * len(y) * np.eye(k), z.T @ (y - mu_y))
    beta = beta_z / sd_x
    return float(mu_y - mu_x @ beta), beta


@dataclass
class Forecast:
    model: str
    outcome: str
    train_start: dt.date
    train_end: dt.date
    actual: pd.Series
    predicted: pd.Series
    params: dict[str, Any] = field(default_factory=dict)

    @property
    def gap_pct(self) -> pd.Series:
        return 100 * (self.actual - self.predicted) / self.predicted

    def window(self, start: dt.date, end: dt.date) -> dict[str, float]:
        idx = self.actual.index
        m = (idx >= pd.Timestamp(start)) & (idx <= pd.Timestamp(end))
        a, p = self.actual[m], self.predicted[m]
        g = self.gap_pct[m].dropna()
        return {
            "n_days": int(m.sum()),
            "mean_actual": float(a.mean()),
            "mean_predicted": float(p.mean()),
            "mean_gap_pct": float(100 * (a.mean() - p.mean()) / p.mean()),
            "rmspe_pct": float(np.sqrt(np.mean(g.to_numpy() ** 2))) if len(g) else float("nan"),
        }

    def monthly_gap_pct(self, start: dt.date) -> pd.Series:
        """Mean gap per calendar month from ``start`` on (ratio of monthly means)."""
        d = pd.DataFrame({"a": self.actual, "p": self.predicted})
        d = d[d.index >= pd.Timestamp(start)]
        m = d.resample("MS").mean()
        return 100 * (m["a"] - m["p"]) / m["p"]


def fit_forecast(
    model: str,
    outcome: str,
    treated: pd.Series,
    donors: pd.DataFrame,
    train_start: dt.date,
    train_end: dt.date,
) -> Forecast:
    idx = treated.index
    donors = donors.reindex(idx)
    usable = donors.notna().all(axis=1).to_numpy() & treated.notna().to_numpy()
    train = (
        np.asarray((idx >= pd.Timestamp(train_start)) & (idx <= pd.Timestamp(train_end))) & usable
    )
    if train.sum() < 60:
        msg = f"{model}/{outcome}: only {int(train.sum())} training days"
        raise ValueError(msg)
    y_tr = treated.to_numpy(dtype=float)[train]
    x_all = donors.to_numpy(dtype=float)
    x_tr = x_all[train]
    params: dict[str, Any] = {}

    if model == "simplex":
        scale_y = float(y_tr.mean())
        scale_x = x_tr.mean(axis=0)
        scale_x = np.where(scale_x != 0, scale_x, 1.0)
        ys, xs = y_tr / scale_y, x_tr / scale_x
        w = fit_weights(ys, xs)
        pred = (x_all / scale_x) @ w * scale_y
        params["weights"] = {
            str(c): float(v) for c, v in zip(donors.columns, w, strict=True) if v > 0.005
        }
    elif model == "ridge":
        n_val = min(VALIDATION_DAYS, int(train.sum()) // 4)
        fit_i, val_i = slice(0, -n_val), slice(-n_val, None)
        errs = {}
        for a in RIDGE_ALPHAS:
            b0, b = _ridge(y_tr[fit_i], x_tr[fit_i], a)
            pv = b0 + x_tr[val_i] @ b
            errs[a] = float(np.mean((y_tr[val_i] - pv) ** 2))
        alpha = min(errs, key=lambda k: errs[k])
        b0, b = _ridge(y_tr, x_tr, alpha)
        pred = b0 + x_all @ b
        params["alpha"] = alpha
        params["validation_mse"] = {str(k): v for k, v in errs.items()}
    else:
        msg = f"unknown model {model}"
        raise ValueError(msg)

    predicted = pd.Series(pred, index=idx)
    predicted[~usable] = np.nan
    return Forecast(
        model, outcome, train_start, train_end, treated.astype(float), predicted, params
    )


def placebo_gaps(
    model: str,
    outcome: str,
    donors: pd.DataFrame,
    train_start: dt.date,
    train_end: dt.date,
    start: dt.date,
    end: dt.date,
) -> pd.Series:
    """Held-out mean gap (%) for each donor refitted as if it were the treated unit."""
    out: dict[str, float] = {}
    for z in donors.columns:
        try:
            f = fit_forecast(
                model, outcome, donors[z], donors.drop(columns=[z]), train_start, train_end
            )
        except (ValueError, np.linalg.LinAlgError):
            continue
        g = f.window(start, end)["mean_gap_pct"]
        if np.isfinite(g):
            out[str(z)] = g
    return pd.Series(out, dtype=float)


def evaluate(
    outcome: str,
    treated: pd.Series,
    donors: pd.DataFrame,
    train_start: dt.date,
    train_end: dt.date,
    windows: dict[str, tuple[dt.date, dt.date]],
    placebo_window: str = "holdout",
) -> tuple[dict[str, Any], dict[str, Forecast]]:
    """Fit every model, evaluate it on each window, and run donor placebos on the
    hold-out window. Returns a JSON-ready block and the fitted forecasts (for figures)."""
    block: dict[str, Any] = {
        "train": [train_start.isoformat(), train_end.isoformat()],
        "windows": {k: [s.isoformat(), e.isoformat()] for k, (s, e) in windows.items()},
        "models": {},
    }
    fits: dict[str, Forecast] = {}
    hs, he = windows[placebo_window]
    for model in MODELS:
        f = fit_forecast(model, outcome, treated, donors, train_start, train_end)
        fits[model] = f
        res: dict[str, Any] = {
            "params": f.params,
            "train": f.window(train_start, train_end),
            "windows": {k: f.window(s, e) for k, (s, e) in windows.items()},
            "monthly_gap_pct": {
                pd.Timestamp(str(k)).strftime("%Y-%m"): (None if pd.isna(v) else float(v))
                for k, v in f.monthly_gap_pct(hs).items()
            },
        }
        pg = placebo_gaps(model, outcome, donors, train_start, train_end, hs, he)
        treated_gap = res["windows"][placebo_window]["mean_gap_pct"]
        res["placebo"] = {
            "window": placebo_window,
            "n": len(pg),
            "abs_gap_median": float(pg.abs().median()) if len(pg) else float("nan"),
            # share of donors whose hold-out drift is at least as large as the CBD's
            "rank_p": float((1 + (pg.abs() >= abs(treated_gap)).sum()) / (1 + len(pg))),
        }
        block["models"][model] = res
    return block, fits


def drift_verdict(block: dict[str, Any], tolerance_pct: float) -> dict[str, Any]:
    """Which models stay within ``tolerance_pct`` on the hold-out year, overall and in
    its last quarter (``holdout_late``, where drift shows)."""
    out = {}
    for model, res in block["models"].items():
        hold = res["windows"]["holdout"]["mean_gap_pct"]
        late_mean = res["windows"]["holdout_late"]["mean_gap_pct"]
        out[model] = {
            "holdout_gap_pct": hold,
            "late_holdout_gap_pct": late_mean,
            "passes": bool(abs(hold) <= tolerance_pct and abs(late_mean) <= tolerance_pct),
        }
    return out
