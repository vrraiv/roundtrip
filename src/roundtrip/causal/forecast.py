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

Two variants added for the redesign (owner, 2026-10-28), reported but not part of the
drift verdict until adopted:

- ``intercept``: non-negative weights with a free intercept and no sum-to-one constraint
  (Doudchenko and Imbens 2016), on donor-scaled series. The intercept absorbs a level
  difference; the weights match the variation.
- ``sdid``: synthetic difference-in-differences (Arkhangelsky et al. 2021): simplex unit
  weights with an intercept and a ridge penalty, plus simplex time weights over the
  training days chosen so that the donors' time-weighted training mean matches their mean
  over the target window. The counterfactual is anchored on the treated unit's
  time-weighted training level, so a level shift late in training is carried forward
  rather than averaged away.

- ``anchored``: the simplex weights with an intercept equal to the mean gap over the
  last ``ANCHOR_DAYS`` of training (a difference-in-differences on the synthetic
  control). This is the level-break correction made explicit: a shift in the CBD's
  level before the end of training is carried forward. With daily data and seasonal
  donors the SDID time weights spread over the whole training window, so ``sdid``
  behaves like ``simplex`` with an intercept; ``anchored`` is the recent-level design.

A plain simplex-with-intercept on mean-scaled series was tried earlier and dropped: every
series has training mean one, so the intercept is zero and the weights are ``simplex``.

``change_point`` tests a daily series (a forecast gap, or the log share of the CBD in the
donor pool) for a single mean shift against a constant and a straight trend.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from scipy.optimize import nnls

from roundtrip.causal.synth import SUM_TO_ONE_PENALTY, fit_weights

MODELS: tuple[str, ...] = ("simplex", "ridge", "intercept", "sdid", "anchored")
ANCHOR_DAYS = 90
# models whose hold-out result decides the drift verdict (the others are reported)
VERDICT_MODELS: tuple[str, ...] = ("simplex", "ridge")
LAMBDA_RIDGE = 1e-3
RIDGE_ALPHAS: tuple[float, ...] = (0.01, 0.1, 1.0, 10.0, 100.0, 1000.0)
VALIDATION_DAYS = 56


def _nnls_intercept(a: np.ndarray, b: np.ndarray) -> tuple[np.ndarray, float]:
    """NNLS on ``a`` with a free intercept (two columns, +1 and -1). Returns the
    non-negative coefficients and the intercept."""
    n, k = a.shape
    aug = np.hstack([a, np.ones((n, 1)), -np.ones((n, 1))])
    sol, _ = nnls(aug, b, maxiter=50 * (k + 2))
    return sol[:k], float(sol[k] - sol[k + 1])


def _intercept(y: np.ndarray, x: np.ndarray) -> tuple[float, np.ndarray]:
    """Non-negative weights, free intercept, no sum constraint."""
    w, a = _nnls_intercept(x, y)
    return a, w


def _sdid(
    y_tr: np.ndarray, x_tr: np.ndarray, x_target: np.ndarray
) -> tuple[float, np.ndarray, np.ndarray]:
    """Synthetic difference-in-differences weights on scaled series.

    Unit weights: simplex with intercept and ridge penalty zeta^2 T_pre, zeta as in
    Arkhangelsky et al. (2021). Time weights: simplex over training days with intercept
    and a small ridge, matching the donors' target-window mean. Returns the intercept
    that anchors the counterfactual on the time-weighted treated level, the unit weights
    and the time weights."""
    t_pre, k = x_tr.shape
    n_post = max(1, len(x_target))
    sigma = float(np.std(np.diff(x_tr, axis=0))) or 1e-6
    zeta = (k * n_post) ** 0.25 * sigma
    pen = np.sqrt(SUM_TO_ONE_PENALTY * t_pre)
    a_unit = np.vstack(
        [
            np.hstack([x_tr, np.ones((t_pre, 1)), -np.ones((t_pre, 1))]),
            np.hstack([np.sqrt(zeta**2 * t_pre) * np.eye(k), np.zeros((k, 2))]),
            np.hstack([pen * np.ones((1, k)), np.zeros((1, 2))]),
        ]
    )
    b_unit = np.concatenate([y_tr, np.zeros(k), [pen]])
    sol, _ = nnls(a_unit, b_unit, maxiter=50 * (k + 2))
    w = sol[:k]
    w = w / w.sum() if w.sum() > 0 else np.full(k, 1.0 / k)

    xbar_post = x_target.mean(axis=0)
    pen_t = np.sqrt(SUM_TO_ONE_PENALTY * k)
    a_time = np.vstack(
        [
            np.hstack([x_tr.T, np.ones((k, 1)), -np.ones((k, 1))]),
            np.hstack([np.sqrt(LAMBDA_RIDGE) * np.eye(t_pre), np.zeros((t_pre, 2))]),
            np.hstack([pen_t * np.ones((1, t_pre)), np.zeros((1, 2))]),
        ]
    )
    b_time = np.concatenate([xbar_post, np.zeros(t_pre), [pen_t]])
    sol_t, _ = nnls(a_time, b_time, maxiter=50 * (t_pre + 2))
    lam = sol_t[:t_pre]
    lam = lam / lam.sum() if lam.sum() > 0 else np.full(t_pre, 1.0 / t_pre)
    intercept = float(lam @ y_tr - (lam @ x_tr) @ w)
    return intercept, w, lam


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


def drift_trend(
    f: Forecast, hold: tuple[dt.date, dt.date], windows: dict[str, tuple[dt.date, dt.date]]
) -> dict[str, Any]:
    """Fit a straight line to the daily hold-out gap (%) and extrapolate its mean over
    each window. This is the gap that continuing pre-policy drift alone would produce."""
    g = f.gap_pct.dropna()
    t0 = pd.Timestamp(hold[0])
    m = (g.index >= t0) & (g.index <= pd.Timestamp(hold[1]))
    idx = pd.DatetimeIndex(g.index[m])
    x = np.asarray((idx - t0).days, dtype=np.float64)
    y = g[m].to_numpy(dtype=float)
    slope, intercept = np.polyfit(x, y, 1)
    out: dict[str, Any] = {"slope_pct_per_month": float(slope * 30.44), "extrapolated": {}}
    for name, (s, e) in windows.items():
        days = np.arange((pd.Timestamp(s) - t0).days, (pd.Timestamp(e) - t0).days + 1)
        out["extrapolated"][name] = float(intercept + slope * days.mean())
    return out


def fit_forecast(
    model: str,
    outcome: str,
    treated: pd.Series,
    donors: pd.DataFrame,
    train_start: dt.date,
    train_end: dt.date,
    target: tuple[dt.date, dt.date] | None = None,
) -> Forecast:
    """``target`` is the window the forecast is for; only ``sdid`` uses it (for its time
    weights). It defaults to everything after the training window."""
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
    scale_y = float(y_tr.mean())
    scale_x = x_tr.mean(axis=0)
    scale_x = np.where(scale_x != 0, scale_x, 1.0)
    ys, xs = y_tr / scale_y, x_tr / scale_x

    if model == "simplex":
        w = fit_weights(ys, xs)
        pred = (x_all / scale_x) @ w * scale_y
        params["weights"] = {
            str(c): float(v) for c, v in zip(donors.columns, w, strict=True) if v > 0.005
        }
    elif model == "anchored":
        w = fit_weights(ys, xs)
        n_late = min(ANCHOR_DAYS, len(ys))
        a = float(np.mean(ys[-n_late:] - xs[-n_late:] @ w))
        pred = (a + (x_all / scale_x) @ w) * scale_y
        params["intercept_pct"] = 100 * a
        params["anchor_days"] = n_late
        params["weights"] = {
            str(c): float(v) for c, v in zip(donors.columns, w, strict=True) if v > 0.005
        }
    elif model == "intercept":
        a, w = _intercept(ys, xs)
        pred = (a + (x_all / scale_x) @ w) * scale_y
        params["intercept_pct"] = 100 * a
        params["weight_sum"] = float(w.sum())
        params["weights"] = {
            str(c): float(v) for c, v in zip(donors.columns, w, strict=True) if v > 0.005
        }
    elif model == "sdid":
        if target is None:
            tmask = np.asarray(idx > pd.Timestamp(train_end)) & usable
        else:
            tmask = (
                np.asarray((idx >= pd.Timestamp(target[0])) & (idx <= pd.Timestamp(target[1])))
                & usable
            )
        if tmask.sum() == 0:
            tmask = np.asarray(idx > pd.Timestamp(train_end)) & usable
        a, w, lam = _sdid(ys, xs, x_all[tmask] / scale_x)
        pred = (a + (x_all / scale_x) @ w) * scale_y
        train_days = pd.DatetimeIndex(idx[train])
        mean_pos = float(lam @ np.arange(len(lam)))
        params["intercept_pct"] = 100 * a
        params["lambda_effective_days"] = float(1.0 / np.sum(lam**2))
        params["lambda_mean_day"] = train_days[round(mean_pos)].strftime("%Y-%m-%d")
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
                model,
                outcome,
                donors[z],
                donors.drop(columns=[z]),
                train_start,
                train_end,
                target=(start, end),
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
        f = fit_forecast(model, outcome, treated, donors, train_start, train_end, target=(hs, he))
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
        res["trend"] = drift_trend(f, (hs, he), windows)
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
    its last quarter (``holdout_late``, where drift shows). ``counts`` marks the models
    whose result decides the verdict; the others are reported for comparison."""
    out = {}
    for model, res in block["models"].items():
        hold = res["windows"]["holdout"]["mean_gap_pct"]
        late_mean = res["windows"]["holdout_late"]["mean_gap_pct"]
        out[model] = {
            "holdout_gap_pct": hold,
            "late_holdout_gap_pct": late_mean,
            "passes": bool(abs(hold) <= tolerance_pct and abs(late_mean) <= tolerance_pct),
            "counts": model in VERDICT_MODELS,
        }
    return out


def verdict_passes(verdict: dict[str, Any]) -> bool:
    """True if any model that counts stays within tolerance."""
    return any(v.get("passes") for v in verdict.values() if v.get("counts", True))


# --- change-point test ----------------------------------------------------------------


def change_point(series: pd.Series, trim: float = 0.15) -> dict[str, Any]:
    """Single mean-shift test on the weekly means of a daily series.

    Compares a constant, a straight line, a step at the best-fitting week (searched with
    ``trim`` of the weeks held back at each end) and a step plus a common trend, by BIC.
    ``sup_f`` is the largest F statistic over candidate break weeks (Quandt), to be
    compared with the same statistic on placebo units rather than a table."""
    weekly = series.dropna().resample("W").mean().dropna()
    n = len(weekly)
    if n < 20:
        return {"n_weeks": n}
    y = weekly.to_numpy(dtype=float)
    t = np.arange(n, dtype=float)
    ssr_const = float(((y - y.mean()) ** 2).sum())
    slope, icpt = np.polyfit(t, y, 1)
    ssr_lin = float(((y - icpt - slope * t) ** 2).sum())
    lo, hi = max(2, int(n * trim)), n - max(2, int(n * trim))
    best: tuple[float, int, float, float] | None = None
    for k in range(lo, hi):
        m1, m2 = float(y[:k].mean()), float(y[k:].mean())
        ssr = float(((y[:k] - m1) ** 2).sum() + ((y[k:] - m2) ** 2).sum())
        if best is None or ssr < best[0]:
            best = (ssr, k, m1, m2)
    assert best is not None
    ssr_step, k, m1, m2 = best
    xst = np.column_stack([np.ones(n), t, (t >= k).astype(float)])
    beta, *_ = np.linalg.lstsq(xst, y, rcond=None)
    ssr_st = float(((y - xst @ beta) ** 2).sum())

    def bic(ssr: float, p: int) -> float:
        return float(n * np.log(max(ssr, 1e-12) / n) + p * np.log(n))

    bics = {
        "constant": bic(ssr_const, 1),
        "linear": bic(ssr_lin, 2),
        "step": bic(ssr_step, 3),
        "step_and_trend": bic(ssr_st, 4),
    }
    return {
        "n_weeks": n,
        "break_week": (weekly.index[k] - pd.Timedelta(days=6)).strftime("%Y-%m-%d"),
        "mean_before": m1,
        "mean_after": m2,
        "shift": m2 - m1,
        "sup_f": float((ssr_const - ssr_step) / (ssr_step / (n - 2))),
        "slope_per_month": float(slope * 52 / 12),
        "step_and_trend_slope_per_month": float(beta[1] * 52 / 12),
        "bic": bics,
        "preferred": min(bics, key=lambda m: bics[m]),
    }
