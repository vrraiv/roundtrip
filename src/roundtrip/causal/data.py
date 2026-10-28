"""Analysis panels built from the zone-hour panel.

Everything starts from zone x day *sufficient statistics* (counts and sums). Outcomes are
ratios of those sums, so aggregating zones into a group or days into a week is a sum of
the statistics followed by the same ratios. That keeps every number in the reports
consistent with every other one.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import duckdb
import numpy as np
import pandas as pd

from roundtrip.config import Settings
from roundtrip.db import connect, read_sql, register_zones

# Sufficient statistics carried through every aggregation.
STATS: tuple[str, ...] = (
    "n_trips",
    "n_airport_trips",
    "n_shared_request",
    "n_wait_valid",
    "wait_sum",
    "fare_sum",
    "pay_sum",
    "tips_sum",
    "cbd_fee_sum",
)

# Outcome name -> (numerator, denominator). ``n_trips`` is a count, not a ratio.
OUTCOME_RATIOS: dict[str, tuple[str, str]] = {
    "mean_wait_seconds": ("wait_sum", "n_wait_valid"),
    "mean_base_fare": ("fare_sum", "n_trips"),
    "mean_driver_pay": ("pay_sum", "n_trips"),
    "tip_rate": ("tips_sum", "fare_sum"),
    "shared_share": ("n_shared_request", "n_trips"),
    "mean_cbd_fee": ("cbd_fee_sum", "n_trips"),
}
OUTCOMES: tuple[str, ...] = ("n_trips", *OUTCOME_RATIOS)

OUTCOME_LABELS: dict[str, str] = {
    "n_trips": "trips",
    "mean_wait_seconds": "mean wait (s)",
    "mean_base_fare": "mean base fare ($)",
    "mean_driver_pay": "mean driver pay ($)",
    "tip_rate": "tip rate",
    "shared_share": "shared-request share",
    "mean_cbd_fee": "mean CBD fee ($)",
}


def add_outcomes(df: pd.DataFrame) -> pd.DataFrame:
    """Add the ratio outcomes to a frame holding the sufficient statistics."""
    out = df.copy()
    for name, (num, den) in OUTCOME_RATIOS.items():
        d = out[den].astype(float)
        out[name] = np.where(d > 0, out[num].astype(float) / d.where(d > 0, 1.0), np.nan)
    return out


@dataclass
class ZoneGroups:
    """Treated and control zone sets (ids), derived from config and the zone lookup."""

    cbd: list[int]
    manhattan_north: list[int]
    inner_bk_queens: list[int]
    airports: list[int]
    names: dict[int, str] = field(default_factory=dict)
    boroughs: dict[int, str] = field(default_factory=dict)

    @property
    def donors(self) -> list[int]:
        """Synthetic-control donor pool: both control groups, airports excluded."""
        return sorted(set(self.manhattan_north) | set(self.inner_bk_queens))

    @property
    def controls(self) -> dict[str, list[int]]:
        return {"manhattan_north": self.manhattan_north, "inner_bk_queens": self.inner_bk_queens}

    def label(self, zone_id: int) -> str:
        return self.names.get(zone_id, str(zone_id))


def zone_groups(settings: Settings, zones: pd.DataFrame) -> ZoneGroups:
    """Build the zone sets from the lookup table (columns zone_id, borough, zone_name)."""
    cbd = set(settings.cbd_zones)
    airports = set(settings.airport_zones)
    excluded = set(settings.manhattan_excluded_zones)
    manhattan = set(zones.loc[zones["borough"] == "Manhattan", "zone_id"].astype(int))
    north = sorted(manhattan - cbd - excluded - airports)
    inner = sorted(set(settings.inner_bk_queens_zones) - cbd - airports)
    names = dict(zip(zones["zone_id"].astype(int), zones["zone_name"], strict=True))
    boroughs = dict(zip(zones["zone_id"].astype(int), zones["borough"], strict=True))
    return ZoneGroups(sorted(cbd), north, inner, sorted(airports), names, boroughs)


def load_zones(settings: Settings, con: duckdb.DuckDBPyConnection | None = None) -> pd.DataFrame:
    con = con or connect(settings)
    register_zones(con, settings, settings.zone_lookup_path)
    return con.execute("SELECT zone_id, borough, zone_name FROM zones ORDER BY zone_id").df()


def load_zone_day(settings: Settings, con: duckdb.DuckDBPyConnection | None = None) -> pd.DataFrame:
    """Zone x day sufficient statistics over every panel month present on disk."""
    con = con or connect(settings)
    sql = read_sql(settings, "causal/zone_day.sql", panel_glob=settings.panel_glob)
    df = con.execute(sql).df()
    df["day"] = pd.to_datetime(df["day"])
    df["zone_id"] = df["zone_id"].astype(int)
    if settings.exclude_airport_trips:
        # OWNER DECISION (open): the panel cannot remove airport-bound trips from zone means,
        # only whole airport pickup zones. Documented in the report.
        df = df[~df["zone_id"].isin(settings.airport_zones)]
    return df.reset_index(drop=True)


def complete_days(df: pd.DataFrame) -> pd.DatetimeIndex:
    """Days with data. The last day of the last month is complete; partial months are not
    produced by the pipeline, so every day present is complete."""
    return pd.DatetimeIndex(sorted(df["day"].unique()))


def group_series(df: pd.DataFrame, zone_ids: list[int], by: str = "day") -> pd.DataFrame:
    """Sum the statistics of ``zone_ids`` by ``by`` and add the outcomes."""
    sub = df[df["zone_id"].isin(zone_ids)]
    agg = sub.groupby(by, as_index=False)[list(STATS)].sum()
    return add_outcomes(agg)


def zone_series(df: pd.DataFrame, outcome: str, zone_ids: list[int]) -> pd.DataFrame:
    """Wide day x zone matrix of one outcome (NaN where a zone has no trips that day)."""
    sub = add_outcomes(df[df["zone_id"].isin(zone_ids)])
    wide = sub.pivot(index="day", columns="zone_id", values=outcome).sort_index()
    return wide.reindex(columns=zone_ids)


def event_week(days: pd.Series, treatment_date: dt.date) -> pd.Series:
    """Week index relative to the treatment date: week 0 starts on the treatment date."""
    delta = (days - pd.Timestamp(treatment_date)).dt.days
    return (delta // 7).astype(int)


def zone_week_panel(df: pd.DataFrame, settings: Settings) -> pd.DataFrame:
    """Zone x event-week panel with outcomes, keeping only complete 7-day weeks."""
    d = df.copy()
    d["week"] = event_week(d["day"], settings.treatment_date)
    days_per_week = d.groupby("week")["day"].nunique()
    full = days_per_week[days_per_week == 7].index
    d = d[d["week"].isin(full)]
    agg = d.groupby(["zone_id", "week"], as_index=False)[list(STATS)].sum()
    out = add_outcomes(agg)
    out["week_start"] = pd.Timestamp(settings.treatment_date) + pd.to_timedelta(
        out["week"] * 7, unit="D"
    )
    return out


def period_of(day: pd.Series, settings: Settings) -> pd.Series:
    """'pre', 'anticipation' or 'post' for each day."""
    ant = pd.Timestamp(settings.anticipation_start)
    treat = pd.Timestamp(settings.treatment_date)
    return pd.Series(
        np.where(day >= treat, "post", np.where(day >= ant, "anticipation", "pre")),
        index=day.index,
    )
