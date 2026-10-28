"""The public site: static HTML for Cloudflare Pages, built from one estimates bundle.

Seven pages (design document, owner decisions 2026-10-28): Home, Trips, Waits, Fares,
Method, Report, Releases. Every number on the site is read from ``REPORTS/estimates.json``
(or an archived ``releases/<month>/estimates.json``); the sentences around the numbers are
fixed templates in this module, so a new release cannot silently change the wording. The
Method page is hand-written Markdown in ``pages/``, reviewed like code. The Report page is
the technical report as the reviewer sees it. No server, no framework, no third-party
scripts, no downloads (readers are pointed at TLC and the repository).

Each approved release is also rendered under ``releases/<month>/`` from its archived bundle,
so a number cited by release month keeps resolving after later releases replace the front
page. The two charts (``charts.py``) are inline SVG built from the bundle, so archived
releases carry them too; the PNG figures are only used by the Report page.
"""

from __future__ import annotations

import datetime as dt
import html
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import markdown

from roundtrip.report.charts import CHART_CSS, CHART_JS, effect_chart, series_chart
from roundtrip.report.summary import DESIGN_LABELS

REPORT_MD = "congestion_pricing.md"
METHOD_MD = "method.md"
NAV = (
    ("index.html", "Home"),
    ("trips.html", "Trips"),
    ("waits.html", "Waits"),
    ("fares.html", "Fares"),
    ("method.html", "Method"),
    ("report.html", "Report"),
    ("releases.html", "Releases"),
)
PURPOSE = (
    "Monthly, reviewed estimates of what congestion pricing did to ride-hail trips, waits "
    "and fares in the Manhattan CBD, from public TLC data, with the uncertainty shown."
)


@dataclass(frozen=True)
class Outcome:
    key: str
    page: str
    title: str
    noun: str  # subject of the finding sentence
    fell: str  # predicate for a fall; {r} is the range, e.g. "5 to 6%"
    rose: str  # predicate for a rise
    extra: str = ""  # a fixed sentence after the finding


OUTCOMES: tuple[Outcome, ...] = (
    Outcome(
        "n_trips",
        "trips.html",
        "Trips",
        "CBD pickups",
        "were {r} lower than in comparison zones",
        "were {r} higher than in comparison zones",
    ),
    Outcome(
        "mean_wait_seconds",
        "waits.html",
        "Waits",
        "Waits for a car",
        "fell {r} relative to comparison zones",
        "rose {r} relative to comparison zones",
    ),
    Outcome(
        "mean_base_fare",
        "fares.html",
        "Fares",
        "Base fares",
        "fell {r} relative to comparison zones",
        "rose {r} relative to comparison zones",
        "The $1.50 CBD fee is charged on top of the base fare and is not included here.",
    ),
)
OUTCOME_BY_KEY = {o.key: o for o in OUTCOMES}

CSS = """
:root { color-scheme: light dark;
        --bg: #fbfbf9; --fg: #1c1c1a; --muted: #6b6b66; --rule: #e2e1dc;
        --accent: #1f6f8b; --accent-soft: #1f6f8b14; --warn: #b25e09; --warn-soft: #b25e0914;
        --card: #ffffff; --panel: #ffffff; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #15171a; --fg: #e8e6e1; --muted: #9a9a94; --rule: #2d3035;
          --accent: #6fb3cc; --accent-soft: #6fb3cc1f; --warn: #e09a4a; --warn-soft: #e09a4a1f;
          --card: #1c1f23; --panel: #1c1f23; }
}
* { box-sizing: border-box; }
html { background: var(--bg); }
body { max-width: 60rem; margin: 0 auto; padding: 0 1rem 3rem; color: var(--fg);
       font: 16px/1.6 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
a { color: var(--accent); text-decoration-thickness: 1px; text-underline-offset: 2px; }
header { display: flex; flex-wrap: wrap; align-items: baseline; gap: 0.5rem 1.5rem;
         padding: 1rem 0 0.6rem; border-bottom: 1px solid var(--rule); margin-bottom: 1.75rem; }
header .brand { font-weight: 700; font-size: 1.15rem; letter-spacing: 0.01em;
                text-decoration: none; color: var(--fg); }
header .brand small { font-weight: 400; color: var(--muted); font-size: 0.85rem;
                      margin-left: 0.6rem; }
nav { display: flex; flex-wrap: wrap; gap: 0.1rem 0.2rem; margin-left: auto; }
nav a { padding: 0.25rem 0.55rem; border-radius: 4px; text-decoration: none;
        color: var(--fg); font-size: 0.95rem; white-space: nowrap; }
nav a:hover { background: var(--accent-soft); }
nav a[aria-current] { color: var(--accent); font-weight: 600;
                      box-shadow: inset 0 -2px 0 var(--accent); border-radius: 4px 4px 0 0; }
h1 { font-size: 1.9rem; line-height: 1.2; margin: 0 0 0.6rem; letter-spacing: -0.01em; }
h2 { font-size: 1.25rem; margin: 2.2rem 0 0.6rem; }
h3 { font-size: 1.05rem; margin: 1.6rem 0 0.4rem; }
p { margin: 0.6rem 0 0.9rem; }
.purpose { font-size: 1.1rem; color: var(--muted); max-width: 44rem; margin-bottom: 1.4rem; }
.status { display: flex; flex-wrap: wrap; align-items: baseline; gap: 0.2rem 0.6rem;
          font-size: 0.88rem; color: var(--muted); padding: 0.45rem 0.8rem; border-radius: 6px;
          background: var(--accent-soft); margin: 0 0 1.4rem; }
.status b { color: var(--fg); }
.status .dot { display: inline-block; width: 0.55rem; height: 0.55rem; border-radius: 50%;
               background: var(--accent); margin-right: 0.35rem; vertical-align: middle;
               position: relative; top: -1px; }
.status.candidate { background: var(--warn-soft); }
.status.candidate .dot { background: var(--warn); }
.status details { flex-basis: 100%; }
.status summary { cursor: pointer; color: var(--accent); }
.status code { font-size: 0.82em; }
.findings { display: grid; gap: 1rem; grid-template-columns: 1fr; margin: 1.2rem 0 1.6rem; }
@media (min-width: 48rem) { .findings { grid-template-columns: repeat(3, 1fr); } }
.finding { background: var(--card); border: 1px solid var(--rule);
           border-top: 3px solid var(--accent); border-radius: 8px;
           padding: 0.9rem 1.1rem 0.6rem; display: flex; flex-direction: column; }
.finding .label { text-transform: uppercase; font-size: 0.75rem; letter-spacing: 0.08em;
                  color: var(--muted); font-weight: 600; }
.finding .range { font-size: 1.7rem; font-weight: 700; margin: 0.15rem 0 0.35rem;
                  letter-spacing: -0.01em; font-variant-numeric: tabular-nums; }
.finding .caveat { display: inline-block; font-size: 0.72rem; font-weight: 600;
                   letter-spacing: 0.04em; text-transform: uppercase; color: var(--warn);
                   background: var(--warn-soft);
                   padding: 0.1rem 0.45rem; border-radius: 999px; vertical-align: middle;
                   margin-left: 0.4rem; }
.finding p { font-size: 0.95rem; margin: 0 0 0.5rem; }
.finding .more { margin-top: auto; font-size: 0.9rem; }
figure { margin: 1.4rem 0; }
figure .panel { background: var(--panel); border-radius: 8px; padding: 0.6rem;
                border: 1px solid var(--rule); }
figcaption { color: var(--muted); font-size: 0.85rem; margin-top: 0.5rem; }
.table-wrap { overflow-x: auto; margin: 1rem 0; }
table { border-collapse: collapse; width: 100%; font-size: 0.92em;
        font-variant-numeric: tabular-nums; }
th, td { border-bottom: 1px solid var(--rule); padding: 0.45rem 0.7rem; text-align: left;
         vertical-align: top; }
th { font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--muted);
     border-bottom: 2px solid var(--rule); }
tbody tr:nth-child(even) { background: var(--accent-soft); }
code { font-size: 0.88em; }
blockquote { margin: 1rem 0; padding: 0.3rem 1rem; border-left: 3px solid var(--rule);
             color: var(--muted); }
.note { color: var(--muted); font-size: 0.9rem; }
.links { margin: 1.4rem 0; }
.links a { margin-right: 1.2rem; }
footer { margin-top: 3rem; padding-top: 1rem; border-top: 1px solid var(--rule);
         font-size: 0.82em; color: var(--muted); line-height: 1.5; }
@media (max-width: 47.99rem) {
  /* phones: wordmark on its own line, the seven links in one row that scrolls sideways */
  header { flex-direction: column; align-items: stretch; gap: 0.3rem; padding-bottom: 0; }
  nav { margin: 0 -1rem; padding: 0 1rem 0.5rem; flex-wrap: nowrap; overflow-x: auto;
        scrollbar-width: none; -webkit-overflow-scrolling: touch; }
  nav::-webkit-scrollbar { display: none; }
  nav a { padding: 0.3rem 0.5rem; font-size: 0.9rem; }
  nav a[aria-current] { border-radius: 4px; }
  h1 { font-size: 1.55rem; }
  .finding .range { font-size: 1.5rem; }
}
"""

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title><style>{css}
{chart_css}</style></head>
<body>
<header><a class="brand" href="{home}">roundtrip<small>NYC ride-hail economics</small></a>
<nav>{nav}</nav></header>
<main>
{body}
</main>
<footer>{footer}</footer>
<script>{js}</script>
</body></html>
"""


# --- bundle access --------------------------------------------------------------------


def load_bundle(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def bundle_run_id(reports_dir: Path) -> str | None:
    p = reports_dir / "estimates.json"
    if not p.exists():
        return None
    run = load_bundle(p).get("run_id")
    return None if run is None else str(run)


def post_estimates(bundle: dict[str, Any], outcome: str) -> list[dict[str, Any]]:
    """The donor-adjusted first-year estimate under each design, in design order."""
    rows = [
        e
        for e in bundle.get("estimates", [])
        if e["outcome"] == outcome and e["window"] == "post" and e.get("adjusted", True)
    ]
    order = list(DESIGN_LABELS)
    return sorted(rows, key=lambda e: order.index(e["design"]) if e["design"] in order else 9)


def effect_range(rows: list[dict[str, Any]]) -> tuple[float, float] | None:
    vals = [float(e["effect_pct"]) for e in rows if e.get("effect_pct") is not None]
    return (min(vals), max(vals)) if vals else None


def drift(bundle: dict[str, Any], outcome: str) -> dict[str, Any] | None:
    """Out-of-sample verdict for the outcome: does any model stay within tolerance over
    the untreated hold-out year? None when the test is not in the bundle."""
    block = (bundle.get("synth_oos") or {}).get(outcome)
    if not block:
        return None
    verdict = block.get("verdict", {})
    simplex = block["models"].get("simplex", {})
    hold = simplex.get("windows", {}).get("holdout", {})
    return {
        "passes": any(v.get("passes") for v in verdict.values() if v.get("counts", True)),
        "holdout_gap_pct": hold.get("mean_gap_pct"),
        "holdout": block.get("windows", {}).get("holdout"),
        "models": verdict,
    }


# --- sentences ------------------------------------------------------------------------


def _pct(x: float, digits: int = 1) -> str:
    return f"{abs(x):.{digits}f}"


def _span(a: float, b: float) -> str:
    """'5 to 6%', or '5%' when both round the same."""
    return f"{_pct(a)} to {_pct(b)}%" if _pct(a) != _pct(b) else f"{_pct(a)}%"


def range_label(rng: tuple[float, float] | None) -> str:
    """'-5 to -6%' style label for the finding block; '+2 to +6%' for a rise."""
    if rng is None:
        return "n/a"
    lo, hi = rng
    if lo < 0 and hi <= 0:
        a, b = sorted((abs(lo), abs(hi)))
        return f"-{_pct(a)} to -{_pct(b)}%" if _pct(a) != _pct(b) else f"-{_pct(a)}%"
    if lo >= 0:
        return f"+{_pct(lo)} to +{_pct(hi)}%" if _pct(lo) != _pct(hi) else f"+{_pct(lo)}%"
    return f"{lo:+.1f} to {hi:+.1f}%"


def finding_sentence(o: Outcome, rng: tuple[float, float] | None, d: dict[str, Any] | None) -> str:
    """The fixed-template sentence for one outcome, numbers from the bundle. The caveat is
    added when no counterfactual model stays within tolerance over the untreated year."""
    if rng is None:
        return f"{o.noun}: no estimate in this release."
    lo, hi = rng
    if lo < 0 and hi <= 0:
        a, b = sorted((abs(lo), abs(hi)))
        s = f"{o.noun} {o.fell.format(r=_span(a, b))} in the first year of the charge."
    elif lo >= 0:
        s = f"{o.noun} {o.rose.format(r=_span(lo, hi))} in the first year of the charge."
    else:
        s = (
            f"{o.noun} moved between {lo:+.1f}% and {hi:+.1f}% relative to comparison zones in "
            "the first year of the charge, depending on the comparison: no clear direction."
        )
    if d is not None and not d["passes"] and d.get("holdout_gap_pct") is not None:
        gap = float(d["holdout_gap_pct"])
        s += (
            " Some or all of this was already under way before the charge: relative to the "
            f"same zones, {o.noun} had drifted by about {gap:+.1f}% over the year before "
            "it, with no policy change. We cannot yet say how much is the policy."
        )
    if o.extra:
        s += " " + o.extra
    return s


# --- banner ---------------------------------------------------------------------------


def latest_approval(releases_dir: Path | None) -> dict[str, Any] | None:
    if releases_dir is None:
        return None
    p = releases_dir / "index.json"
    if not p.exists():
        return None
    approved = json.loads(p.read_text(encoding="utf-8")).get("approved", [])
    if not approved:
        return None
    entry: dict[str, Any] = max(approved, key=lambda e: str(e.get("month", "")))
    return entry


def approval_banner(entry: dict[str, Any] | None, run_id: str | None = None) -> str:
    """Markdown placed above every page: who approved which run, and their notes. A report
    built from a run other than the approved one (a candidate preview) says so instead."""
    if entry is None or (run_id is not None and str(entry.get("run_id")) != run_id):
        text = (
            "> **Not yet reviewed.** These estimates are a candidate and have not been "
            "approved by the reviewer."
        )
        if run_id is not None:
            text += f" Candidate run `{run_id}`."
        if entry is not None:
            text += f" The last approved release is {entry['month']} (run `{entry['run_id']}`)."
        return text + "\n\n"
    text = (
        f"> **Reviewed release {entry['month']}.** Approved by {entry['reviewer']} on "
        f"{entry['date']} ({entry['status']}); run `{entry['run_id']}`, git "
        f"`{entry['git_sha']}`, data through {entry['data_end']}."
    )
    notes = str(entry.get("notes") or "").strip()
    if notes:
        text += f"\n>\n> Reviewer's notes: {notes}"
    return text + "\n\n"


def _banner_html(entry: dict[str, Any] | None, run_id: str | None) -> str:
    """One status line under the title: reviewed release or candidate, with the run ids and
    the reviewer's notes folded away."""
    e = html.escape
    if entry is None or (run_id is not None and str(entry.get("run_id")) != run_id):
        text = (
            '<b><span class="dot"></span>Not yet reviewed.</b> These estimates are a candidate '
            "and have not been approved by the reviewer."
        )
        if run_id is not None:
            text += f" Candidate run <code>{e(run_id)}</code>."
        if entry is not None:
            text += (
                f" The last approved release is {e(str(entry['month']))} "
                f"(run <code>{e(str(entry['run_id']))}</code>)."
            )
        return f'<div class="status candidate">{text}</div>'
    text = (
        f'<b><span class="dot"></span>Reviewed release {e(str(entry["month"]))}.</b> '
        f"Approved by {e(str(entry['reviewer']))} on {e(str(entry['date']))} "
        f"({e(str(entry['status']))}); data through {e(str(entry['data_end']))}."
    )
    notes = str(entry.get("notes") or "").strip()
    details = (
        f"<summary>Reviewer's notes and run ids</summary><p>Reviewer's notes: {e(notes)}</p>"
        if notes
        else "<summary>Run ids</summary>"
    )
    details += (
        f"<p>Run <code>{e(str(entry['run_id']))}</code>, git "
        f"<code>{e(str(entry['git_sha']))}</code>, manifest "
        f"<code>{e(str(entry.get('manifest_hash', '')))}</code>.</p>"
    )
    return f'<div class="status">{text}<details>{details}</details></div>'


# --- pages ----------------------------------------------------------------------------


def _md(text: str) -> str:
    out = markdown.markdown(text, extensions=["tables", "fenced_code"])
    # wide tables scroll sideways on a phone instead of breaking the layout
    return out.replace("<table>", '<div class="table-wrap"><table>').replace(
        "</table>", "</table></div>"
    )


def _nav(current: str, prefix: str = "") -> str:
    return " ".join(
        f'<a href="{prefix}{href}"{' aria-current="page"' if href == current else ""}>{label}</a>'
        for href, label in NAVIGATION
    )


NAVIGATION = NAV


def _footer(bundle: dict[str, Any], entry: dict[str, Any] | None, repo_url: str) -> str:
    who = f"Estimates reviewed by {html.escape(str(entry['reviewer']))}. " if entry else ""
    return (
        "Data: NYC TLC High Volume For-Hire Vehicle trip records, "
        f"{html.escape(str(bundle.get('data_start', '')))} to "
        f"{html.escape(str(bundle.get('data_end', '')))}. {who}"
        f'Code and method: <a href="{html.escape(repo_url)}">{html.escape(repo_url)}</a> '
        f"(MIT). Run <code>{html.escape(str(bundle.get('run_id', '')))}</code>, "
        f"git <code>{html.escape(str(bundle.get('git_sha', ''))[:12])}</code>."
    )


def _finding_block(o: Outcome, bundle: dict[str, Any]) -> str:
    rows = post_estimates(bundle, o.key)
    rng = effect_range(rows)
    d = drift(bundle, o.key)
    caveat = '<span class="caveat">see caveat</span>' if d is not None and not d["passes"] else ""
    return (
        '<div class="finding">'
        f'<div class="label">{o.title}</div>'
        f'<div class="range">{html.escape(range_label(rng))}{caveat}</div>'
        f"<p>{html.escape(finding_sentence(o, rng, d))}</p>"
        f'<a class="more" href="{o.page}">{o.title} in detail →</a></div>'
    )


def home_html(bundle: dict[str, Any]) -> str:
    parts = [
        "<h1>Congestion pricing and ride-hail in the Manhattan CBD</h1>",
        f'<p class="purpose">{PURPOSE}</p>',
        '<div class="findings">' + "".join(_finding_block(o, bundle) for o in OUTCOMES) + "</div>",
    ]
    parts.append(effect_chart([(o.title, post_estimates(bundle, o.key)) for o in OUTCOMES]))
    w = bundle.get("windows", {})
    post = w.get("post", {})
    parts.append(
        f'<p class="note">First year: {html.escape(str(post.get("start", "")))} to '
        f"{html.escape(str(post.get('end', '')))}. Comparison zones: the rest of Manhattan "
        "below 96th Street and the inner Brooklyn and Queens zones across the East River, "
        "airports excluded. Three comparisons are shown because they rest on different "
        "assumptions; the range across them is the honest summary.</p>"
    )
    parts.append(
        '<p class="links"><a href="method.html">How we measure it</a>'
        '<a href="report.html">Full report</a><a href="releases.html">Releases</a></p>'
    )
    return "\n".join(parts)


def _estimates_table(bundle: dict[str, Any], outcome: str) -> str:
    rows = [
        e
        for e in bundle.get("estimates", [])
        if e["outcome"] == outcome and e.get("adjusted", True)
    ]
    order = list(DESIGN_LABELS)
    rows.sort(key=lambda e: (order.index(e["design"]) if e["design"] in order else 9, e["window"]))
    out = [
        '<div class="table-wrap"><table><thead><tr><th>Comparison</th><th>Window</th>'
        "<th>Effect</th><th>95% interval</th><th>Days</th></tr></thead><tbody>"
    ]
    for e in rows:
        out.append(
            f"<tr><td>{html.escape(DESIGN_LABELS.get(e['design'], e['design']))}</td>"
            f"<td>{html.escape(e['window'])}</td><td>{float(e['effect_pct']):+.1f}%</td>"
            f"<td>[{float(e['lo_pct']):+.1f}, {float(e['hi_pct']):+.1f}]</td>"
            f"<td>{int(e.get('n_days', 0))}</td></tr>"
        )
    out.append("</tbody></table></div>")
    return "".join(out)


def outcome_html(o: Outcome, bundle: dict[str, Any]) -> str:
    rows = post_estimates(bundle, o.key)
    rng = effect_range(rows)
    d = drift(bundle, o.key)
    parts = [
        f"<h1>{o.title}</h1>",
        f'<p class="purpose">{html.escape(finding_sentence(o, rng, d))}</p>',
    ]
    parts.append(series_chart(bundle, o.key, o.title))
    parts.append("<h2>Estimates</h2>")
    parts.append(
        "<p>Each comparison nets the CBD against comparable zones outside it. The anticipation "
        "window runs from the charge's announcement to the day before it started.</p>"
    )
    parts.append(_estimates_table(bundle, o.key))
    parts.append("<h2>Can the comparison be trusted?</h2>")
    if d is None:
        parts.append("<p>The out-of-sample test is not in this release.</p>")
    else:
        hold = d.get("holdout") or ["", ""]
        gap = d.get("holdout_gap_pct")
        verdicts = ", ".join(
            f"{html.escape(m)}: {'within' if v.get('passes') else 'outside'} tolerance"
            for m, v in d["models"].items()
        )
        parts.append(
            "<p>We fit the counterfactual on 2023 only and check it against the year that "
            f"followed ({html.escape(str(hold[0]))} to {html.escape(str(hold[1]))}), when "
            "nothing changed in policy. A trustworthy comparison stays close to the actual "
            "series there. "
            + (
                f"Over that year the CBD ran about {float(gap):+.1f}% from its forecast. "
                if gap is not None
                else ""
            )
            + f"Models: {verdicts}. "
            + (
                "The comparison passes."
                if d["passes"]
                else "No model passes, so part of the first-year gap may be drift that "
                "predates the charge; the estimates above should be read as an upper bound on "
                "the policy's effect in this direction."
            )
            + "</p>"
        )
    parts.append(
        '<p class="links"><a href="method.html">How we measure it</a>'
        '<a href="report.html">Full report with diagnostics</a></p>'
    )
    return "\n".join(parts)


def releases_html(
    releases_dir: Path | None, bundle: dict[str, Any], repo_url: str, archive: list[str]
) -> str:
    index: dict[str, list[dict[str, Any]]] = {"approved": [], "rejected": []}
    if releases_dir is not None and (releases_dir / "index.json").exists():
        index.update(json.loads((releases_dir / "index.json").read_text(encoding="utf-8")))
    rows = [dict(e, _kind="approved") for e in index.get("approved", [])]
    rows += [dict(e, _kind="rejected") for e in index.get("rejected", [])]
    rows.sort(key=lambda e: str(e.get("month", "")), reverse=True)
    parts = [
        "<h1>Releases</h1>",
        "<p>One row per reviewed run. A release replaces the front page; earlier releases "
        "stay at their own address so a number cited by month keeps resolving.</p>",
        '<div class="table-wrap"><table><thead><tr><th>Release</th><th>Decision</th>'
        "<th>Reviewer</th><th>Date</th><th>Data through</th><th>Run</th><th>Notes</th></tr>"
        "</thead><tbody>",
    ]
    for e in rows:
        month = str(e.get("month", ""))
        link = f'<a href="releases/{month}/index.html">{month}</a>' if month in archive else month
        status = str(e.get("status", e["_kind"]))
        notes = str(e.get("notes") or e.get("reason") or "")
        parts.append(
            f"<tr><td>{link}</td><td>{html.escape(status)}</td>"
            f"<td>{html.escape(str(e.get('reviewer', '')))}</td>"
            f"<td>{html.escape(str(e.get('date', '')))}</td>"
            f"<td>{html.escape(str(e.get('data_end', '')))}</td>"
            f"<td><code>{html.escape(str(e.get('run_id', '')))}</code> "
            f"git <code>{html.escape(str(e.get('git_sha', '')))}</code></td>"
            f"<td>{html.escape(notes)}</td></tr>"
        )
    if not rows:
        parts.append('<tr><td colspan="7">No reviewed release yet.</td></tr>')
    parts.append("</tbody></table></div>")
    sha = str(bundle.get("git_sha", ""))[:12]
    parts += [
        "<h2>Getting the data</h2>",
        "<p>Nothing is downloadable here. The trip records are public: the "
        '<a href="https://www.nyc.gov/site/tlc/about/tlc-trip-record-data.page">TLC trip '
        "record page</a> publishes one parquet file per month for high-volume for-hire "
        "vehicles (Uber and Lyft). Every number on this site is reproducible from those files "
        f'and the <a href="{html.escape(repo_url)}">repository</a>: <code>uv run roundtrip '
        "backfill</code> builds the zone-day aggregates, <code>uv run roundtrip run-causal"
        "</code> produces the estimates bundle, and the README walks through it.</p>",
        "<h2>Citing a release</h2>",
        f"<p>roundtrip, release {html.escape(str(latest_month(index)))}, estimates bundle at "
        f'git <code>{html.escape(sha)}</code>, <a href="{html.escape(repo_url)}">'
        f"{html.escape(repo_url)}</a>. Licence: MIT for the code; the TLC data carry their "
        "own terms.</p>",
    ]
    return "\n".join(parts)


def latest_month(index: dict[str, list[dict[str, Any]]]) -> str:
    approved = index.get("approved", [])
    return max((str(e.get("month", "")) for e in approved), default="none")


def _md_page(path: Path | None, title: str, missing: str) -> tuple[str, str]:
    if path is None or not path.exists():
        return title, f"<h1>{title}</h1><p>{missing}</p>"
    text = path.read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
            break
    return title, _md(text)


# --- build ----------------------------------------------------------------------------


def _write(
    out_dir: Path,
    name: str,
    title: str,
    body: str,
    banner: str,
    nav: str,
    footer: str,
    home: str = "index.html",
) -> Path:
    # the status line sits under the page title, not above it
    body = body.replace("</h1>", "</h1>\n" + banner, 1) if "</h1>" in body else banner + body
    page = PAGE.format(
        title=html.escape(title),
        css=CSS,
        chart_css=CHART_CSS,
        js=CHART_JS,
        nav=nav,
        home=home,
        body=body,
        footer=footer,
    )
    dest = out_dir / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(page, encoding="utf-8")
    return dest


def render_release(
    out_dir: Path,
    bundle: dict[str, Any],
    entry: dict[str, Any] | None,
    repo_url: str,
    nav_prefix: str = "",
) -> list[Path]:
    """Home and the three outcome pages for one bundle. Used for the live site and for each
    archived release; the charts are built from the bundle."""
    banner = _banner_html(entry, str(bundle.get("run_id")))
    footer = _footer(bundle, entry, repo_url)
    written = [
        _write(
            out_dir,
            "index.html",
            "Congestion pricing and ride-hail in the Manhattan CBD",
            home_html(bundle),
            banner,
            _nav("index.html", nav_prefix),
            footer,
            home=f"{nav_prefix}index.html",
        )
    ]
    for o in OUTCOMES:
        written.append(
            _write(
                out_dir,
                o.page,
                f"{o.title}: congestion pricing and ride-hail in the CBD",
                outcome_html(o, bundle),
                banner,
                _nav(o.page, nav_prefix),
                footer,
                home=f"{nav_prefix}index.html",
            )
        )
    return written


def build_site(
    reports_dir: Path,
    out_dir: Path,
    releases_dir: Path | None = None,
    pages_dir: Path | None = None,
    repo_url: str = "https://github.com/vrraiv/roundtrip",
) -> list[Path]:
    """Build the whole site into ``out_dir``: the live pages from ``reports_dir``, the
    Method page from ``pages_dir``, the Releases page and one archived copy per approved
    release found under ``releases_dir``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    bundle_path = reports_dir / "estimates.json"
    bundle = load_bundle(bundle_path) if bundle_path.exists() else {}
    entry = latest_approval(releases_dir)
    written = render_release(out_dir, bundle, entry, repo_url)
    # the Report page embeds the PNG figures from the report build
    figures = reports_dir / "figures"
    if figures.exists():
        (out_dir / "figures").mkdir(parents=True, exist_ok=True)
        for f in figures.glob("*.png"):
            shutil.copy2(f, out_dir / "figures" / f.name)

    # archived releases, each from its own bundle
    archive: list[str] = []
    if releases_dir is not None and releases_dir.exists():
        index = (
            json.loads((releases_dir / "index.json").read_text(encoding="utf-8"))
            if (releases_dir / "index.json").exists()
            else {}
        )
        for e in index.get("approved", []):
            month = str(e.get("month", ""))
            p = releases_dir / month / "estimates.json"
            if not p.exists():
                continue
            written += render_release(
                out_dir / "releases" / month, load_bundle(p), e, repo_url, "../../"
            )
            archive.append(month)

    banner = _banner_html(entry, str(bundle.get("run_id")) if bundle else None)
    footer = _footer(bundle, entry, repo_url)
    title, body = _md_page(
        pages_dir / METHOD_MD if pages_dir else None,
        "How we measure it",
        "The method page has not been written yet.",
    )
    written.append(_write(out_dir, "method.html", title, body, banner, _nav("method.html"), footer))
    title, body = _md_page(
        reports_dir / REPORT_MD, "Full report", "The technical report is not in this build."
    )
    written.append(_write(out_dir, "report.html", title, body, banner, _nav("report.html"), footer))
    written.append(
        _write(
            out_dir,
            "releases.html",
            "Releases",
            releases_html(releases_dir, bundle, repo_url, archive),
            banner,
            _nav("releases.html"),
            footer,
        )
    )
    written.append(
        _write(out_dir, "404.html", "Not found", "<h1>Not found</h1>", "", _nav(""), footer)
    )
    stamp = dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    (out_dir / "build.txt").write_text(f"built {stamp} from run {bundle.get('run_id')}\n")
    return written
