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

Two candidate designs for the redesign (owner decisions of 2026-10-28), reported beside
the two above but not part of the drift verdict until adopted:

- ``anchored``: the simplex weights with an intercept equal to the mean gap over the
  last ``ANCHOR_DAYS`` of training (a difference-in-differences on the synthetic
  control). This is the level-break correction made explicit: a shift in the CBD's
  level before the end of training is carried forward.
- ``growth``: growth-rate synthetic control. Simplex weights fitted on week-over-week
  changes in log trips, so the donors are chosen for their dynamics and the level is
  free; the counterfactual is a weighted geometric index of the donors pinned to the
  CBD's mean log level over the last ``ANCHOR_DAYS`` of training.

Two seasonally anchored designs (owner decision 9, 2026-10-29), after the rolling-origin
validation showed the forecast gap has a calendar shape the donors do not share:

- ``seasonal``: the simplex weights plus a calendar-month profile of the training
  residual over the last ``SEASON_DAYS`` of training (de-meaned over the months seen;
  months not seen get zero) and a level over the last ``ANCHOR_DAYS`` net of that
  profile. The anchored design with the season taken out of the anchor.
- ``seasonal_step``: the same, after netting out a single level shift inside the
  training window when ``change_point`` prefers a step on the training residual.
  Without this a break inside the profile's twelve months is read as seasonality.

Two more were run through the 2023 hold-out on 28 October 2026 and dropped: a simplex
with a free intercept and no sum constraint (Doudchenko and Imbens 2016) reproduced the
simplex to the second decimal, and synthetic difference-in-differences (Arkhangelsky et
al. 2021) rested on time weights with ten effective days out of 317. A plain intercept on
mean-scaled series is zero by construction.

``change_point`` tests a daily series (a forecast gap, or the log share of the CBD in the
donor pool) for a single mean shift against a constant and a straight trend.

``rolling_origin`` is the selection rule for the trips design: train through each of
several origins, score the untreated days 1 to 3, 4 to 6 and 7 to 12 months ahead, and
pass a design only if its mean gap across origins is within tolerance at every horizon.
With one origin any estimator can be tuned to pass; with six it has to forecast. The
same statistics over the origins from a given date on (``after``) are reported beside
the rule, so a level break that no earlier origin could see can be read separately.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from roundtrip.causal.synth import fit_weights

MODELS: tuple[str, ...] = (
    "simplex",
    "ridge",
    "anchored",
    "growth",
    "seasonal",
    "seasonal_step",
)
ANCHOR_DAYS = 90
SEASON_DAYS = 365
STEP_MONTH_DAYS = 20  # a year-month counts in the step comparison with this many days
GROWTH_LAG_DAYS = 7
# models whose hold-out result decides the drift verdict (the others are reported)
VERDICT_MODELS: tuple[str, ...] = ("simplex", "ridge")
RIDGE_ALPHAS: tuple[float, ...] = (0.01, 0.1, 1.0, 10.0, 100.0, 1000.0)
VALIDATION_DAYS = 56


def _growth(
    y_tr: np.ndarray, x_tr: np.ndarray, train_days: pd.DatetimeIndex
) -> tuple[float, np.ndarray]:
    """Simplex weights on ``GROWTH_LAG_DAYS``-day changes in logs (pairs of days a lag
    apart, so a missing day drops the pair rather than shifting it), and the log level
    offset over the last ``ANCHOR_DAYS`` of training."""
    logs = pd.DataFrame(np.column_stack([np.log(y_tr), np.log(x_tr)]), index=train_days)
    lagged = logs.shift(GROWTH_LAG_DAYS, freq="D").reindex(logs.index)
    d = (logs - lagged).dropna().to_numpy()
    w = fit_weights(d[:, 0], d[:, 1:])
    n_late = min(ANCHOR_DAYS, len(logs))
    tail = logs.to_numpy()[-n_late:]
    a = float(np.mean(tail[:, 0] - tail[:, 1:] @ w))
    return a, w


def _seasonal(
    resid: pd.Series, allow_step: bool
) -> tuple[float, dict[int, float], dict[str, Any] | None, int]:
    """Calendar-month profile of the training residual (treated minus synthetic, on
    the mean-scaled series) over the last ``SEASON_DAYS`` of training, de-meaned over
    the months present, and the level over the last ``ANCHOR_DAYS`` net of that
    profile. With ``allow_step`` a single level shift inside training is netted out first,
    the days before the break lifted to the level after it, so a break is not read as
    seasonality. The break week is where ``change_point`` prefers a step (with or without
    a trend beside it, since a seasonal residual can look trended over part of a year); the
    shift is the mean difference between the same calendar months after and before it, not
    the step test's own estimate, which the season would contaminate (months with at
    least ``STEP_MONTH_DAYS`` days on each side, in different years), because within one
    year a step and a seasonal profile are not separable. With no such month the step is
    left alone and the design reduces to ``seasonal``."""
    step: dict[str, Any] | None = None
    r = resid.astype(float).copy()
    if allow_step:
        cp = change_point(r)
        if cp.get("preferred") in ("step", "step_and_trend"):
            brk = pd.Timestamp(cp["break_week"])
            shift, months = _same_month_shift(r, brk)
            if months:
                r[r.index < brk] += shift
                step = {
                    "break_week": cp["break_week"],
                    "shift_pct": 100 * shift,
                    "months": months,
                }
    last = r[r.index > r.index[-1] - pd.Timedelta(days=SEASON_DAYS)]
    by_month = last.groupby(last.index.month).mean()
    profile = {int(m): float(v - by_month.mean()) for m, v in by_month.items()}
    d = np.array([profile.get(int(m), 0.0) for m in pd.DatetimeIndex(r.index).month])
    n_late = min(ANCHOR_DAYS, len(r))
    a = float(np.mean(r.to_numpy()[-n_late:] - d[-n_late:]))
    return a, profile, step, n_late


def _same_month_shift(r: pd.Series, brk: pd.Timestamp) -> tuple[float, list[int]]:
    """Level shift at ``brk`` measured on calendar months seen on both sides: the mean,
    over those months, of the month's mean after the break minus its mean before. Only
    year-months with at least ``STEP_MONTH_DAYS`` days count, which keeps a month split by
    the break itself out of the comparison."""
    idx = pd.DatetimeIndex(r.index)
    df = pd.DataFrame(
        {
            "v": r.to_numpy(dtype=float),
            "ym": idx.year * 100 + idx.month,
            "month": idx.month,
            "after": idx >= brk,
        }
    )
    g = df.groupby(["ym", "month", "after"])["v"].agg(["mean", "size"]).reset_index()
    before: dict[int, list[float]] = {}
    after: dict[int, list[float]] = {}
    kept = g[g["size"] >= STEP_MONTH_DAYS]
    for month, is_after, v in zip(
        kept["month"].to_numpy(), kept["after"].to_numpy(), kept["mean"].to_numpy(), strict=True
    ):
        (after if bool(is_after) else before).setdefault(int(month), []).append(float(v))
    months = sorted(set(before) & set(after))
    if not months:
        return 0.0, []
    return float(np.mean([np.mean(after[m]) - np.mean(before[m]) for m in months])), months


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
) -> Forecast:
    """Fit ``model`` on the training window and forecast every usable day of ``treated``
    with its parameters frozen."""
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
    elif model in ("seasonal", "seasonal_step"):
        w = fit_weights(ys, xs)
        resid = pd.Series(ys - xs @ w, index=pd.DatetimeIndex(idx[train]))
        a, profile, step, n_late = _seasonal(resid, model == "seasonal_step")
        d = np.array([profile.get(int(m), 0.0) for m in pd.DatetimeIndex(idx).month])
        pred = (a + d + (x_all / scale_x) @ w) * scale_y
        params["intercept_pct"] = 100 * a
        params["anchor_days"] = n_late
        params["profile_pct"] = {str(m): 100 * v for m, v in sorted(profile.items())}
        params["step"] = step
        params["weights"] = {
            str(c): float(v) for c, v in zip(donors.columns, w, strict=True) if v > 0.005
        }
    elif model == "growth":
        if (y_tr <= 0).any() or (x_tr <= 0).any():
            msg = f"{model}/{outcome}: non-positive values in the training window"
            raise ValueError(msg)
        a, w = _growth(y_tr, x_tr, pd.DatetimeIndex(idx[train]))
        positive = (x_all > 0).all(axis=1)
        pred = np.full(len(idx), np.nan)
        pred[positive] = np.exp(a + np.log(x_all[positive]) @ w)
        params["level_ratio"] = float(np.exp(a))
        params["anchor_days"] = min(ANCHOR_DAYS, int(train.sum()))
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


# --- rolling-origin validation --------------------------------------------------------


def horizon_window(
    origin: dt.date, months: tuple[int, int], end: dt.date
) -> tuple[dt.date, dt.date] | None:
    """Calendar days from ``months[0]`` to ``months[1]`` months after ``origin``, cut at
    ``end`` (the last untreated day). None when nothing is left."""
    base = pd.Timestamp(origin) + pd.Timedelta(days=1)
    s = (base + pd.DateOffset(months=months[0] - 1)).date()
    e = min((base + pd.DateOffset(months=months[1]) - pd.Timedelta(days=1)).date(), end)
    return (s, e) if e >= s else None


def _horizon_label(months: tuple[int, int]) -> str:
    return f"{months[0]}-{months[1]}"


def rolling_origin(
    outcome: str,
    treated: pd.Series,
    donors: pd.DataFrame,
    train_start: dt.date,
    origins: tuple[dt.date, ...],
    horizons: tuple[tuple[int, int], ...],
    end: dt.date,
    min_days: int,
    tolerance_pct: float,
    models: tuple[str, ...] = MODELS,
    after: dt.date | None = None,
) -> dict[str, Any]:
    """Rolling-origin validation of every model.

    For each origin, train from ``train_start`` through the origin and score the forecast
    over each horizon (months ahead of the origin, cut at ``end``); windows shorter than
    ``min_days`` are skipped. Every donor is refitted as treated at every origin, so each
    scored window carries the share of donors whose own gap is at least as large as the
    treated unit's. A model passes if the mean gap across origins is within
    ``tolerance_pct`` at every horizon; among the passers the lowest mean RMSPE is
    ``selected``. With ``after``, ``summary_after`` and ``selected_after`` repeat the
    summary over the origins on or after that date, beside the rule."""
    labels = [_horizon_label(h) for h in horizons]
    block: dict[str, Any] = {
        "train_start": train_start.isoformat(),
        "end": end.isoformat(),
        "origins": [o.isoformat() for o in origins],
        "horizons": {lab: list(h) for lab, h in zip(labels, horizons, strict=True)},
        "tolerance_pct": tolerance_pct,
        "min_days": min_days,
        "fits": {m: {} for m in models},
        "summary": {},
    }
    windows: dict[dt.date, dict[str, tuple[dt.date, dt.date]]] = {}
    for o in origins:
        windows[o] = {}
        for lab, h in zip(labels, horizons, strict=True):
            win = horizon_window(o, h, end)
            if win is not None and (win[1] - win[0]).days + 1 >= min_days:
                windows[o][lab] = win
    block["holiday_windows"] = sum(
        s <= dt.date(s.year, 12, 25) <= e for ws in windows.values() for (s, e) in ws.values()
    )

    for model in models:
        for o in origins:
            if not windows[o]:
                continue
            try:
                f = fit_forecast(model, outcome, treated, donors, train_start, o)
            except (ValueError, np.linalg.LinAlgError) as exc:
                block["fits"][model][o.isoformat()] = {"error": str(exc)}
                continue
            placebo: dict[str, list[float]] = {lab: [] for lab in windows[o]}
            for z in donors.columns:
                try:
                    pf = fit_forecast(
                        model, outcome, donors[z], donors.drop(columns=[z]), train_start, o
                    )
                except (ValueError, np.linalg.LinAlgError):
                    continue
                for lab, (s, e) in windows[o].items():
                    g = pf.window(s, e)["mean_gap_pct"]
                    if np.isfinite(g):
                        placebo[lab].append(abs(g))
            entry: dict[str, Any] = {
                "train_rmspe_pct": f.window(train_start, o)["rmspe_pct"],
                "params": {k: v for k, v in f.params.items() if k != "weights"},
                "horizons": {},
            }
            for lab, (s, e) in windows[o].items():
                w = f.window(s, e)
                pg = np.asarray(placebo[lab])
                entry["horizons"][lab] = {
                    "window": [s.isoformat(), e.isoformat()],
                    "n_days": w["n_days"],
                    "mean_gap_pct": w["mean_gap_pct"],
                    "rmspe_pct": w["rmspe_pct"],
                    "placebo_n": len(pg),
                    "placebo_rank_p": float(
                        (1 + (pg >= abs(w["mean_gap_pct"])).sum()) / (1 + len(pg))
                    ),
                }
            block["fits"][model][o.isoformat()] = entry

    block["summary"] = {
        m: _rolling_summary(block["fits"][m], labels, tolerance_pct) for m in models
    }
    block["ranking"] = _passing(block["summary"])
    block["selected"] = block["ranking"][0] if block["ranking"] else None
    if after is not None:
        keep = {o.isoformat() for o in origins if o >= after}
        block["after_origin"] = after.isoformat()
        block["summary_after"] = {
            m: _rolling_summary(block["fits"][m], labels, tolerance_pct, keep) for m in models
        }
        ranking = _passing(block["summary_after"])
        block["selected_after"] = ranking[0] if ranking else None
    return block


def _rolling_summary(
    fits: dict[str, Any],
    labels: list[str],
    tolerance_pct: float,
    origins: set[str] | None = None,
) -> dict[str, Any]:
    """Mean gap, largest gap, RMSPE and placebo share per horizon across the scored
    origins (all of them, or those in ``origins``), and whether the model passes."""
    summary: dict[str, Any] = {"horizons": {}}
    all_rmspe: list[float] = []
    for lab in labels:
        rows = [
            e["horizons"][lab]
            for o, e in fits.items()
            if (origins is None or o in origins) and "horizons" in e and lab in e["horizons"]
        ]
        if not rows:
            summary["horizons"][lab] = {"n_origins": 0}
            continue
        gaps = np.array([r["mean_gap_pct"] for r in rows])
        rmspes = [r["rmspe_pct"] for r in rows]
        all_rmspe += rmspes
        summary["horizons"][lab] = {
            "n_origins": len(rows),
            "bias_pct": float(gaps.mean()),
            "max_abs_gap_pct": float(np.abs(gaps).max()),
            "rmspe_pct": float(np.mean(rmspes)),
            "rank_p": float(np.mean([r["placebo_rank_p"] for r in rows])),
            "within": bool(abs(gaps.mean()) <= tolerance_pct),
        }
    scored = [h for h in summary["horizons"].values() if h["n_origins"]]
    summary["rmspe_pct"] = float(np.mean(all_rmspe)) if all_rmspe else float("nan")
    summary["passes"] = bool(scored) and all(h["within"] for h in scored)
    return summary


def _passing(summary: dict[str, Any]) -> list[str]:
    return sorted(
        (m for m, s in summary.items() if s["passes"]), key=lambda m: summary[m]["rmspe_pct"]
    )


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
