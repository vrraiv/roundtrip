"""The three comparisons the owner asked for (2026-10-27), on CBD daily series.

For each outcome and for each of the two treated windows (anticipation, post):

- ``prior_year``: the counterfactual is the CBD itself on the same weekdays one year
  (364 days) earlier.
- ``pre_window``: the counterfactual is the CBD over the clean pre-event window
  (June 2024 to 13 November 2024).
- ``synthetic_control``: the counterfactual is the synthetic CBD (see ``synth.py``).

Each of the first two comes in a raw form and a donor-adjusted form that divides out the
same change measured on the donor pool (a difference-in-differences in logs, reported as
a percentage). Intervals for those two are weekly block bootstraps; the synthetic
control's interval comes from the placebo-in-space gap distribution.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from roundtrip.causal.forecast import MODEL_LABELS
from roundtrip.causal.synth import PlaceboInSpace, SynthFit
from roundtrip.config import Settings

BLOCK_DAYS = 7


@dataclass
class Estimate:
    design: str
    window: str
    outcome: str
    adjusted: bool
    actual: float
    counterfactual: float
    effect: float
    effect_pct: float
    lo_pct: float
    hi_pct: float
    n_days: int
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def _mask(series: pd.Series, start: dt.date, end: dt.date) -> np.ndarray:
    idx = pd.DatetimeIndex(series.index)
    return np.asarray((idx >= pd.Timestamp(start)) & (idx <= pd.Timestamp(end)))


def _blocks(x: np.ndarray) -> list[np.ndarray]:
    n = int(np.ceil(len(x) / BLOCK_DAYS))
    return [x[i * BLOCK_DAYS : (i + 1) * BLOCK_DAYS] for i in range(n)]


def _pct(log_mean: float) -> float:
    return float(100 * (np.exp(log_mean) - 1))


def _boot_paired(diffs: np.ndarray, settings: Settings) -> tuple[float, float]:
    """Percentile interval for 100*(exp(mean(diffs)) - 1), resampling 7-day blocks."""
    d = diffs[np.isfinite(diffs)]
    if len(d) < 2 * BLOCK_DAYS:
        return float("nan"), float("nan")
    blocks = _blocks(d)
    rng = np.random.default_rng(settings.bootstrap_seed)
    means = np.empty(settings.bootstrap_reps)
    for r in range(settings.bootstrap_reps):
        pick = rng.integers(0, len(blocks), size=len(blocks))
        means[r] = np.concatenate([blocks[i] for i in pick]).mean()
    return _pct(float(np.percentile(means, 2.5))), _pct(float(np.percentile(means, 97.5)))


def _boot_two_sample(
    target: np.ndarray,
    base: np.ndarray,
    donor_target: np.ndarray | None,
    donor_base: np.ndarray | None,
    settings: Settings,
) -> tuple[float, float]:
    """Interval for the (donor-adjusted) log change in means, resampling 7-day blocks
    independently in the target and base windows."""
    bt, bb = _blocks(target), _blocks(base)
    dbt = _blocks(donor_target) if donor_target is not None else None
    dbb = _blocks(donor_base) if donor_base is not None else None
    rng = np.random.default_rng(settings.bootstrap_seed)
    out = np.empty(settings.bootstrap_reps)
    for r in range(settings.bootstrap_reps):
        it = rng.integers(0, len(bt), size=len(bt))
        ib = rng.integers(0, len(bb), size=len(bb))
        st = np.nanmean(np.concatenate([bt[i] for i in it]))
        sb = np.nanmean(np.concatenate([bb[i] for i in ib]))
        est = np.log(st / sb)
        if dbt is not None and dbb is not None:
            dt_ = np.nanmean(np.concatenate([dbt[i] for i in it]))
            db_ = np.nanmean(np.concatenate([dbb[i] for i in ib]))
            est -= np.log(dt_ / db_)
        out[r] = est
    return _pct(float(np.percentile(out, 2.5))), _pct(float(np.percentile(out, 97.5)))


def prior_year(
    cbd: pd.Series,
    donors: pd.Series,
    outcome: str,
    window: str,
    start: dt.date,
    end: dt.date,
    settings: Settings,
) -> list[Estimate]:
    target = pd.date_range(start, end, freq="D")
    counter = target - pd.Timedelta(days=364)
    a = cbd.reindex(target).to_numpy(dtype=float)
    c = cbd.reindex(counter).to_numpy(dtype=float)
    da = donors.reindex(target).to_numpy(dtype=float)
    dc = donors.reindex(counter).to_numpy(dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        raw_d = np.log(a / c)
        adj_d = raw_d - np.log(da / dc)
    n = int(np.isfinite(raw_d).sum())
    ma, mc = float(np.nanmean(a)), float(np.nanmean(c))
    raw_pct = _pct(float(np.nanmean(raw_d)))
    lo, hi = _boot_paired(raw_d, settings)
    raw = Estimate(
        "prior_year",
        window,
        outcome,
        False,
        ma,
        mc,
        ma - mc,
        raw_pct,
        lo,
        hi,
        n,
        "same weekdays 364 days earlier",
    )
    adj_pct = _pct(float(np.nanmean(adj_d)))
    cf = ma / (1 + adj_pct / 100)
    lo2, hi2 = _boot_paired(adj_d, settings)
    adjusted = Estimate(
        "prior_year",
        window,
        outcome,
        True,
        ma,
        cf,
        ma - cf,
        adj_pct,
        lo2,
        hi2,
        int(np.isfinite(adj_d).sum()),
        "CBD year-over-year ratio divided by the donor-pool year-over-year ratio",
    )
    return [raw, adjusted]


def pre_window(
    cbd: pd.Series,
    donors: pd.Series,
    outcome: str,
    window: str,
    start: dt.date,
    end: dt.date,
    settings: Settings,
) -> list[Estimate]:
    pre_end = settings.anticipation_start - dt.timedelta(days=1)
    pre = _mask(cbd, settings.pre_window_start, pre_end)
    tgt = _mask(cbd, start, end)
    a_v, c_v = cbd.to_numpy(dtype=float)[tgt], cbd.to_numpy(dtype=float)[pre]
    da_v, dc_v = donors.to_numpy(dtype=float)[tgt], donors.to_numpy(dtype=float)[pre]
    ma, mc = float(np.nanmean(a_v)), float(np.nanmean(c_v))
    raw_pct = 100 * (ma - mc) / mc
    lo, hi = _boot_two_sample(a_v, c_v, None, None, settings)
    raw = Estimate(
        "pre_window",
        window,
        outcome,
        False,
        ma,
        mc,
        ma - mc,
        raw_pct,
        lo,
        hi,
        int(tgt.sum()),
        "CBD mean over the window versus CBD mean over June to 13 Nov 2024",
    )
    donor_ratio = float(np.nanmean(da_v) / np.nanmean(dc_v))
    cf = mc * donor_ratio
    adj_pct = 100 * (ma / cf - 1)
    lo2, hi2 = _boot_two_sample(a_v, c_v, da_v, dc_v, settings)
    adjusted = Estimate(
        "pre_window",
        window,
        outcome,
        True,
        ma,
        cf,
        ma - cf,
        adj_pct,
        lo2,
        hi2,
        int(tgt.sum()),
        "CBD change divided by the donor-pool change over the same windows",
    )
    return [raw, adjusted]


def from_synth(fit: SynthFit, placebo: PlaceboInSpace, outcome: str, window: str) -> Estimate:
    w = fit.windows[window]
    lo, hi = placebo.interval_pct()
    return Estimate(
        "synthetic_control",
        window,
        outcome,
        True,
        w["mean_actual"],
        w["mean_synthetic"],
        w["mean_gap"],
        w["mean_gap_pct"],
        lo,
        hi,
        int(w["n_days"]),
        f"{MODEL_LABELS.get(fit.design, fit.design)}; placebo-in-space p = "
        f"{placebo.p_value:.2f} on the RMSPE ratio; interval from the placebo gap spread",
    )
