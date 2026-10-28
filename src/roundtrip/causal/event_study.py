"""Zone-week event study with two-way fixed effects (brief section 6a).

Treated zones are the CBD; controls are one of the two zone groups. Three pieces:

- a static difference-in-differences with two treated-period dummies (anticipation window
  and post), reference = clean pre-period;
- a dynamic specification with one dummy per event week (binned at the window ends),
  reference = the last full week before the announcement;
- placebo versions: fake treatment dates in 2024 using only pre-announcement data, and fake
  treated zones drawn from the control group.

Standard errors are clustered by zone (pyfixest CRV1).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
import pyfixest as pf
from scipy import stats

from roundtrip.causal.data import OUTCOMES, event_week
from roundtrip.config import Settings

ES_OUTCOMES: tuple[str, ...] = tuple(o for o in OUTCOMES if o != "mean_cbd_fee")


def _transform(df: pd.DataFrame, outcome: str) -> pd.Series:
    """Counts enter in logs; everything else in levels."""
    if outcome == "n_trips":
        return pd.Series(np.log(df[outcome].astype(float).where(df[outcome] > 0)), index=df.index)
    return df[outcome].astype(float)


@dataclass
class Coef:
    name: str
    estimate: float
    se: float
    lo: float
    hi: float
    p: float

    def to_dict(self) -> dict[str, float | str]:
        return self.__dict__.copy()


def _coef(fit: Any, name: str) -> Coef:
    t = fit.tidy()
    row = t.loc[name]
    return Coef(
        name=name,
        estimate=float(row["Estimate"]),
        se=float(row["Std. Error"]),
        lo=float(row["2.5%"]),
        hi=float(row["97.5%"]),
        p=float(row["Pr(>|t|)"]),
    )


def _joint_p(fit: Any, names: list[str]) -> float:
    """Wald test that the named coefficients are jointly zero (chi-square)."""
    b = fit.coef().loc[names].to_numpy(dtype=float)
    v = pd.DataFrame(fit._vcov, index=fit.coef().index, columns=fit.coef().index)
    vv = v.loc[names, names].to_numpy(dtype=float)
    try:
        w = float(b @ np.linalg.solve(vv, b))
    except np.linalg.LinAlgError:
        return float("nan")
    return float(stats.chi2.sf(w, df=len(names)))


@dataclass
class EventStudyResult:
    outcome: str
    control_group: str
    n_zones_treated: int
    n_zones_control: int
    n_weeks: int
    static: dict[str, Coef]  # 'anticipation', 'post'
    dynamic: list[Coef]  # one per relative week, name = 'w-30'...'w+30'
    dynamic_weeks: list[int]
    reference_week: int
    pretrend_p: float  # joint test of the clean-pre leads
    transform: str = "level"
    extras: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "control_group": self.control_group,
            "n_zones_treated": self.n_zones_treated,
            "n_zones_control": self.n_zones_control,
            "n_weeks": self.n_weeks,
            "transform": self.transform,
            "static": {k: c.to_dict() for k, c in self.static.items()},
            "dynamic": [
                {"week": w, **c.to_dict()}
                for w, c in zip(self.dynamic_weeks, self.dynamic, strict=True)
            ],
            "reference_week": self.reference_week,
            "pretrend_p": self.pretrend_p,
            **self.extras,
        }


def _prepare(
    weeks: pd.DataFrame,
    outcome: str,
    treated_zones: list[int],
    control_zones: list[int],
    settings: Settings,
    treatment_date: dt.date | None = None,
    anticipation_start: dt.date | None = None,
    end_date: dt.date | None = None,
) -> tuple[pd.DataFrame, int]:
    treatment_date = treatment_date or settings.treatment_date
    anticipation_start = anticipation_start or settings.anticipation_start
    d = weeks[weeks["zone_id"].isin(treated_zones + control_zones)].copy()
    if end_date is not None:
        d = d[d["week_start"] <= pd.Timestamp(end_date)]
    d["treated"] = d["zone_id"].isin(treated_zones).astype(int)
    d["y"] = _transform(d, outcome)
    # relative week to the (possibly fake) treatment date
    d["rel"] = event_week(d["week_start"], treatment_date)
    ant_week = int(
        event_week(pd.Series([pd.Timestamp(anticipation_start)]), treatment_date).iloc[0]
    )
    d["post"] = (d["rel"] >= 0).astype(int)
    d["anticipation"] = ((d["rel"] >= ant_week) & (d["rel"] < 0)).astype(int)
    d = d.dropna(subset=["y"])
    return d, ant_week


def event_study(
    weeks: pd.DataFrame,
    outcome: str,
    treated_zones: list[int],
    control_zones: list[int],
    settings: Settings,
    control_group: str,
    treatment_date: dt.date | None = None,
    anticipation_start: dt.date | None = None,
    end_date: dt.date | None = None,
) -> EventStudyResult:
    d, ant_week = _prepare(
        weeks,
        outcome,
        treated_zones,
        control_zones,
        settings,
        treatment_date,
        anticipation_start,
        end_date,
    )
    d["t_ant"] = d["treated"] * d["anticipation"]
    d["t_post"] = d["treated"] * d["post"]
    static_fit = pf.feols("y ~ t_ant + t_post | zone_id + rel", data=d, vcov={"CRV1": "zone_id"})
    static = {"anticipation": _coef(static_fit, "t_ant"), "post": _coef(static_fit, "t_post")}

    # Dynamic: one dummy per relative week inside the window, binned at the ends.
    lo, hi = -settings.event_weeks_before, settings.event_weeks_after
    ref = ant_week - 1  # last full week before the announcement
    d["relb"] = d["rel"].clip(lower=lo, upper=hi)
    present = sorted(w for w in d["relb"].unique() if w != ref)
    cols: list[str] = []
    for w in present:
        name = f"w{w:+d}".replace("+", "p").replace("-", "m")
        d[name] = ((d["relb"] == w) & (d["treated"] == 1)).astype(int)
        cols.append(name)
    dyn_fit = pf.feols(f"y ~ {' + '.join(cols)} | zone_id + rel", data=d, vcov={"CRV1": "zone_id"})
    dynamic = [_coef(dyn_fit, c) for c in cols]
    clean_leads = [c for c, w in zip(cols, present, strict=True) if lo < w < ant_week]
    pre_p = _joint_p(dyn_fit, clean_leads) if clean_leads else float("nan")

    return EventStudyResult(
        outcome=outcome,
        control_group=control_group,
        n_zones_treated=int(d.loc[d["treated"] == 1, "zone_id"].nunique()),
        n_zones_control=int(d.loc[d["treated"] == 0, "zone_id"].nunique()),
        n_weeks=int(d["rel"].nunique()),
        static=static,
        dynamic=dynamic,
        dynamic_weeks=[int(w) for w in present],
        reference_week=ref,
        pretrend_p=pre_p,
        transform="log" if outcome == "n_trips" else "level",
        extras={"anticipation_week": ant_week, "n_obs": len(d)},
    )


def placebo_dates(
    weeks: pd.DataFrame,
    outcome: str,
    treated_zones: list[int],
    control_zones: list[int],
    settings: Settings,
    control_group: str,
) -> list[dict[str, Any]]:
    """Static DiD with fake treatment dates, using only data before the real announcement.

    The fake anticipation window has the same length as the real one.
    """
    out = []
    ant_len = settings.treatment_date - settings.anticipation_start
    end = settings.anticipation_start - dt.timedelta(days=1)
    for fake in settings.placebo_dates:
        d, _ = _prepare(
            weeks,
            outcome,
            treated_zones,
            control_zones,
            settings,
            treatment_date=fake,
            anticipation_start=fake - ant_len,
            end_date=end,
        )
        d["t_post"] = d["treated"] * d["post"]
        fit = pf.feols("y ~ t_post | zone_id + rel", data=d, vcov={"CRV1": "zone_id"})
        c = _coef(fit, "t_post")
        out.append(
            {
                "kind": "fake_date",
                "date": fake.isoformat(),
                "control_group": control_group,
                **c.to_dict(),
            }
        )
    return out


def placebo_zones(
    weeks: pd.DataFrame,
    outcome: str,
    control_zones: list[int],
    settings: Settings,
    control_group: str,
    n_draws: int = 20,
    seed: int = 0,
) -> list[dict[str, Any]]:
    """Static DiD with fake treated zones drawn from the control group (true dates).

    Each draw treats a random third of the control zones; the rest are controls.
    """
    rng = np.random.default_rng(seed)
    out = []
    k = max(2, len(control_zones) // 3)
    for i in range(n_draws):
        fake = sorted(int(z) for z in rng.choice(control_zones, size=k, replace=False))
        rest = [z for z in control_zones if z not in fake]
        d, _ = _prepare(weeks, outcome, fake, rest, settings)
        d["t_post"] = d["treated"] * d["post"]
        fit = pf.feols("y ~ t_post | zone_id + rel", data=d, vcov={"CRV1": "zone_id"})
        c = _coef(fit, "t_post")
        out.append(
            {
                "kind": "fake_zones",
                "draw": i,
                "control_group": control_group,
                "n_fake": k,
                **c.to_dict(),
            }
        )
    return out
