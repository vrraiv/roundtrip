"""Markdown tables from an estimates bundle (used by the job summary and the report)."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from roundtrip.causal.data import OUTCOME_LABELS

DESIGN_LABELS = {
    "prior_year": "prior year",
    "synthetic_control": "synthetic control",
    "pre_window": "pre-window",
}


def load_bundle(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def _fmt(x: float | None, digits: int = 1) -> str:
    if x is None or math.isnan(x):
        return "n/a"
    return f"{x:+.{digits}f}"


def _label(o: str) -> str:
    return OUTCOME_LABELS.get(o, o)


def comparison_table(bundle: dict[str, Any], window: str = "post") -> str:
    """Donor-adjusted estimates per outcome and design for one window."""
    rows = [e for e in bundle["estimates"] if e["window"] == window and e["adjusted"]]
    outcomes = list(dict.fromkeys(e["outcome"] for e in rows))
    designs = [
        d for d in bundle["design"]["comparison_designs"] if any(e["design"] == d for e in rows)
    ]
    head = "| Outcome | " + " | ".join(DESIGN_LABELS.get(d, d) for d in designs) + " |"
    lines = [head, "|---|" + "---|" * len(designs)]
    for o in outcomes:
        cells = []
        for d in designs:
            m = [e for e in rows if e["outcome"] == o and e["design"] == d]
            if not m:
                cells.append("n/a")
                continue
            e = m[-1]
            cells.append(f"{_fmt(e['effect_pct'])}% [{_fmt(e['lo_pct'])}, {_fmt(e['hi_pct'])}]")
        lines.append(f"| {_label(o)} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def synth_table(bundle: dict[str, Any]) -> str:
    lines = [
        "| Outcome | Pre-RMSPE | Anticipation gap | Post gap | Placebo p (post) "
        "| 2023 test gap | Top donors |",
        "|---|---|---|---|---|---|---|",
    ]
    for o, s in bundle["synth"].items():
        w = s["windows"]
        ant = w.get("anticipation", {}).get("mean_gap_pct")
        post = w.get("post", {}).get("mean_gap_pct")
        p = s["placebo"].get("post", {}).get("p_value")
        t23 = s.get("test_2023", {}).get("windows", {}).get("placebo_2023", {}).get("mean_gap_pct")
        top = ", ".join(f"{k.split(' ', 1)[-1]} {v:.2f}" for k, v in list(s["weights"].items())[:4])
        p_txt = "n/a" if p is None else f"{p:.2f}"
        lines.append(
            f"| {_label(o)} | {s['pre_rmspe']:.3g} | {_fmt(ant)}% | {_fmt(post)}% | "
            f"{p_txt} | {_fmt(t23)}% | {top} |"
        )
    return "\n".join(lines)


def event_study_table(bundle: dict[str, Any]) -> str:
    lines = [
        "| Outcome | Control group | Anticipation | Post | Pre-trend p |",
        "|---|---|---|---|---|",
    ]
    for g, outcomes in bundle["event_study"].items():
        for o, r in outcomes.items():
            a, p = r["static"]["anticipation"], r["static"]["post"]
            unit = " log pts" if r["transform"] == "log" else ""
            a_txt = f"{_fmt(a['estimate'], 3)} [{_fmt(a['lo'], 3)}, {_fmt(a['hi'], 3)}]{unit}"
            p_txt = f"{_fmt(p['estimate'], 3)} [{_fmt(p['lo'], 3)}, {_fmt(p['hi'], 3)}]{unit}"
            lines.append(
                f"| {_label(o)} | {g.replace('_', ' ')} | {a_txt} | {p_txt} | "
                f"{r['pretrend_p']:.2f} |"
            )
    return "\n".join(lines)


def placebo_table(bundle: dict[str, Any]) -> str:
    rows = bundle["placebos"]
    if not rows:
        return "_no placebo rows_"
    lines = [
        "| Kind | Control group | n | Mean estimate | Share with p < 0.05 |",
        "|---|---|---|---|---|",
    ]
    for kind in ("fake_date", "fake_zones"):
        for g in sorted({r["control_group"] for r in rows}):
            sub = [r for r in rows if r["kind"] == kind and r["control_group"] == g]
            if not sub:
                continue
            mean = sum(r["estimate"] for r in sub) / len(sub)
            sig = sum(r["p"] < 0.05 for r in sub) / len(sub)
            lines.append(
                f"| {kind.replace('_', ' ')} | {g.replace('_', ' ')} | {len(sub)} | "
                f"{mean:+.3f} | {100 * sig:.0f}% |"
            )
    return "\n".join(lines)


def bundle_summary(bundle: dict[str, Any]) -> str:
    sha = str(bundle.get("git_sha"))[:8]
    parts = [
        f"**Run** `{bundle['run_id']}` on data {bundle['data_start']} to {bundle['data_end']} "
        f"({len(bundle['months'])} months, git `{sha}`, {bundle['elapsed_seconds']} s).",
        "",
        "### Post-period effects on the CBD, % of counterfactual (donor-adjusted; 95% intervals)",
        "",
        comparison_table(bundle, "post"),
    ]
    if "anticipation" in bundle["windows"]:
        parts += [
            "",
            "### Anticipation window (14 Nov 2024 to 4 Jan 2025)",
            "",
            comparison_table(bundle, "anticipation"),
        ]
    parts += ["", "### Synthetic control", "", synth_table(bundle)]
    parts += [
        "",
        "### Event study (zone and week fixed effects; SE clustered by zone)",
        "",
        event_study_table(bundle),
    ]
    parts += ["", "### Placebo tests (static post coefficient)", "", placebo_table(bundle)]
    return "\n".join(parts) + "\n"
