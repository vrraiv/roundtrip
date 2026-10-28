"""Build ``REPORTS/congestion_pricing.md`` from an estimates bundle.

The report follows the brief (section 9): question, data, identification, results with
intervals, diagnostics, limitations, what the reviewer should look at, and the review
dashboard (current versus last approved estimates, placebo p-values, validation table,
what changed). Interpretation that belongs to the owner is marked as such.
"""

from __future__ import annotations

import datetime as dt
import json
import shutil
from pathlib import Path
from typing import Any

from roundtrip.causal.data import OUTCOME_LABELS
from roundtrip.config import Settings
from roundtrip.report.summary import (
    DESIGN_LABELS,
    _fmt,
    comparison_table,
    event_study_table,
    placebo_table,
    synth_table,
)

REPORT_NAME = "congestion_pricing.md"
HEADLINE = ("n_trips", "mean_wait_seconds", "mean_base_fare")


def _headline_sentence(bundle: dict[str, Any]) -> str:
    post = bundle["windows"].get("post")
    if not post:
        return "No post-treatment data yet."
    bits = []
    for o in HEADLINE:
        e = bundle["headline"].get(o)
        if not e:
            continue
        bits.append(
            f"{OUTCOME_LABELS[o]} {_fmt(e['effect_pct'])}% "
            f"[{_fmt(e['lo_pct'])}, {_fmt(e['hi_pct'])}]"
        )
    return (
        f"Relative to the synthetic control, CBD pickups from {post['start']} to {post['end']} "
        "show: " + "; ".join(bits) + " (percent of counterfactual, 95% placebo intervals)."
    )


def _validation_table(settings: Settings, months: list[str]) -> str:
    """Status per month from the validation JSON files (derived dir first, repo second)."""
    lines = ["| Month | Status | Rows | Failed or warned checks |", "|---|---|---|---|"]
    found = 0
    for m in months:
        candidates = [settings.validation_path(m), settings.repo_dir / "validation" / f"{m}.json"]
        p = next((c for c in candidates if c.exists()), None)
        if p is None:
            continue
        found += 1
        r = json.loads(p.read_text(encoding="utf-8"))
        flagged = [c["name"] for c in r.get("checks", []) if not c.get("passed", True)]
        lines.append(
            f"| {m} | {r.get('status', '?')} | {r.get('row_count', 0):,} | "
            f"{', '.join(flagged) if flagged else 'none'} |"
        )
    if not found:
        return "_No validation reports found alongside this run._"
    return "\n".join(lines)


def _what_changed(bundle: dict[str, Any], prev: dict[str, Any] | None) -> str:
    if prev is None:
        return "_No approved release yet; this is the first candidate._"
    lines = [
        f"Last approved run `{prev['run_id']}` covered data to {prev['data_end']}; this run "
        f"covers data to {bundle['data_end']}.",
        "",
        "| Outcome | Design | Last approved | This run | Change (pp) |",
        "|---|---|---|---|---|",
    ]
    prev_rows = {
        (e["outcome"], e["design"], e["window"], e["adjusted"]): e for e in prev["estimates"]
    }
    for e in bundle["estimates"]:
        if e["window"] != "post" or not e["adjusted"]:
            continue
        key = (e["outcome"], e["design"], e["window"], e["adjusted"])
        p = prev_rows.get(key)
        if p is None:
            continue
        lines.append(
            f"| {OUTCOME_LABELS.get(e['outcome'], e['outcome'])} | "
            f"{DESIGN_LABELS.get(e['design'], e['design'])} | "
            f"{_fmt(p['effect_pct'])}% [{_fmt(p['lo_pct'])}, {_fmt(p['hi_pct'])}] | "
            f"{_fmt(e['effect_pct'])}% [{_fmt(e['lo_pct'])}, {_fmt(e['hi_pct'])}] | "
            f"{_fmt(e['effect_pct'] - p['effect_pct'])} |"
        )
    new_months = sorted(set(bundle["months"]) - set(prev.get("months", [])))
    if new_months:
        lines += ["", f"New months since the last approved release: {', '.join(new_months)}."]
    return "\n".join(lines)


def _synth_diag(bundle: dict[str, Any]) -> str:
    out = []
    for o, s in bundle["synth"].items():
        t = s.get("test_2023", {}).get("windows", {}).get("placebo_2023")
        p_post = s["placebo"].get("post", {}).get("p_value")
        if t is None:
            continue
        out.append(
            f"- **{OUTCOME_LABELS.get(o, o)}**: in the 2023 test window the synthetic CBD "
            f"missed the actual CBD by {_fmt(t['mean_gap_pct'])}% on average (RMSPE ratio "
            f"{t['rmspe_ratio']:.2f}). Any post-period gap smaller than that in absolute value "
            f"should not be read as an effect. Placebo-in-space p-value for the post window: "
            f"{'n/a' if p_post is None else f'{p_post:.2f}'}."
        )
    return "\n".join(out) if out else "_2023 test not available (needs data through January 2024)._"


def build_report(
    settings: Settings,
    bundle_path: Path,
    prev_bundle_path: Path | None = None,
    out_dir: Path | None = None,
) -> Path:
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    prev = (
        json.loads(prev_bundle_path.read_text(encoding="utf-8"))
        if prev_bundle_path is not None and prev_bundle_path.exists()
        else None
    )
    out_dir = out_dir or settings.reports_dir
    fig_src = bundle_path.parent / "figures"
    fig_dst = out_dir / "figures"
    fig_dst.mkdir(parents=True, exist_ok=True)
    if fig_src.exists():
        for f in fig_src.glob("*.png"):
            shutil.copy2(f, fig_dst / f.name)

    d = bundle["design"]
    g = bundle["groups"]
    post = bundle["windows"].get("post", {})
    ant = bundle["windows"].get("anticipation", {})
    n_days_post = (
        post
        and (dt.date.fromisoformat(post["end"]) - dt.date.fromisoformat(post["start"])).days + 1
    )

    sha8 = str(bundle["git_sha"])[:8]
    md = f"""# Congestion pricing and ride-hail in the Manhattan CBD

_Candidate report, run `{bundle["run_id"]}` ({bundle["generated_at"]}), git `{sha8}`,
data {bundle["data_start"]} to {bundle["data_end"]} ({len(bundle["months"])} months)._

**Headline.** {_headline_sentence(bundle)}

![Post-period effects under the three comparisons](figures/comparisons.png)

## Question

What happened to ride-hail (Uber and Lyft) activity in the Manhattan Central Business
District when congestion pricing took effect on {d["treatment_date"]}: how many trips started
there, how long riders waited, what they paid, what drivers earned? The policy was
announced on {d["anticipation_start"]}, so the weeks in between are treated as a separate
anticipation period rather than as clean "before" weeks.

## Data

TLC High Volume For-Hire Vehicle trip records, every trip, {bundle["data_start"]} to
{bundle["data_end"]}. The pipeline streams each monthly file, validates it (schema, row
counts, null rates, timestamps, money, zones, licence codes) and reduces it to a pickup
zone by hour by company panel; the analyses here start from that panel. Outcomes are
daily or weekly ratios of sums, so every number is consistent with every other.

- Treated unit: pickups in the {len(g["cbd"])} CBD taxi zones (MTA's list, south of 60th St).
- Control zones: {len(g["manhattan_north"])} Manhattan zones outside the CBD (parks and
  islands excluded) and {len(g["inner_bk_queens"])} inner Brooklyn and Queens zones along the
  East River. Airports are never controls. Airport-bound trips remain in the CBD outcomes
  (owner decision pending; `exclude_airport_trips={d["exclude_airport_trips"]}`).
- Fare measure: TLC `base_passenger_fare`, which excludes tolls, taxes, fees and the
  congestion fee itself. The fee is tabulated separately in the panel.

## Identification

Three comparisons, all reported (owner design, see METHODS.md):

1. **Prior year.** The same weekdays 364 days earlier, with the CBD's year-over-year
   change divided by the donor pool's year-over-year change. Interval: weekly block
   bootstrap.
2. **Synthetic control.** A weighted combination of control zones that tracks the CBD
   from {bundle["synth"]["n_trips"]["fit_start"]} to {bundle["synth"]["n_trips"]["fit_end"]}
   (the day before the announcement). Weights are non-negative and sum to one on
   mean-scaled daily series. Inference is placebo-in-space: every donor is refitted as if
   treated. The design is first tested on the same calendar window a year earlier
   ({d["placebo_window"][0]} to {d["placebo_window"][1]}), when nothing happened.
3. **Pre-window.** CBD means over the window versus the clean pre-period from
   {d["pre_window_start"]} to the day before the announcement, with the donor pool's change
   over the same two windows divided out.

Alongside these, a zone-week two-way fixed-effects event study (zone and week effects,
standard errors clustered by zone, {d["event_weeks"][0]} leads and {d["event_weeks"][1]} lags binned
at the ends) against each control group, with a joint test of the clean leads, fake
treatment dates in 2024 and fake treated zones as placebos.

The post window for the comparisons is the first {n_days_post or 0} days after
{d["treatment_date"]} (through {post.get("end", "n/a")}), so that the prior-year design never
compares a treated day with another treated day. The event study uses all weeks.

## Results

### Post period ({post.get("start", "n/a")} to {post.get("end", "n/a")})

Effects on the CBD as a percent of the counterfactual, donor-adjusted, 95% intervals.

{comparison_table(bundle, "post")}

### Anticipation window ({ant.get("start", "n/a")} to {ant.get("end", "n/a")})

{comparison_table(bundle, "anticipation") if ant else "_not in data_"}

### Synthetic control

{synth_table(bundle)}

![Synthetic control: trips](figures/synth_n_trips.png)

![Synthetic control: mean wait](figures/synth_mean_wait_seconds.png)

![Synthetic control: mean base fare](figures/synth_mean_base_fare.png)

### Event study

{event_study_table(bundle)}

![Event study, Manhattan north control](figures/event_study_manhattan_north.png)

![Event study, inner Brooklyn/Queens control](figures/event_study_inner_bk_queens.png)

## Diagnostics

### The 2023 test of the synthetic control

{_synth_diag(bundle)}

### Pre-trends

The joint test of the clean leads in the event study is reported per outcome and control
group in the table above (column "Pre-trend p"). A small p-value means the CBD and the
control group were already moving differently before the announcement, after zone and
week effects; the dynamic plots show where. Where that happens the fixed-effects estimate
should be read with the synthetic control, which re-weights donors to match the CBD's
pre-period path.

### Placebo tests

Static post coefficients when the treatment date is moved to a fake date in 2024 (using
only pre-announcement data), and when the treated zones are a random third of the control
group (true dates). The share of placebo estimates with p < 0.05 should be near 5%; a
higher share means the clustered standard errors understate the noise for that outcome.

{placebo_table(bundle)}

## Limitations

- This is an aggregate zone-level design. It identifies the change in CBD pickups relative
  to a counterfactual built from other zones; it does not separate riders who switched
  mode from riders who switched pickup location to just outside the CBD. Spillover to the
  control zones would bias the estimates towards finding larger effects.
- The anticipation window overlaps the holiday season, where the CBD's pattern differs
  from the control zones' (visible in the event-study plots at Thanksgiving and Christmas).
  The prior-year design handles this best; the pre-window design least.
- The synthetic control's 2023 test gap bounds what the design can resolve.
- Airport-bound trips are included in the CBD means (owner decision pending).
- Benchmark comparison with published estimates: **placeholder, owner to supply**.

## What the reviewer should look at

1. The three post-period estimates for trips, wait and fare: do they agree in sign and
   roughly in size? Where they disagree, the diagnostics say which to trust.
2. The 2023 test gaps and the placebo-in-space p-values.
3. The pre-trend p-values and the dynamic plots: is there a visible break at the
   announcement, at the treatment date, or at neither?
4. The validation table and the "What changed" table below.

## Review dashboard

### Validation of the months in this run

{_validation_table(settings, bundle["months"])}

### What changed since the last approved release

{_what_changed(bundle, prev)}

### Run record

| Field | Value |
|---|---|
| Run id | `{bundle["run_id"]}` |
| Git SHA | `{bundle["git_sha"]}` |
| Manifest hash | `{bundle["manifest_hash"]}` |
| Package version | {bundle["package_version"]} |
| Analysis wall time | {bundle["elapsed_seconds"]} s |
"""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / REPORT_NAME
    path.write_text(md, encoding="utf-8")
    shutil.copy2(bundle_path, out_dir / "estimates.json")
    return path
