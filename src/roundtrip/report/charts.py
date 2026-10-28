"""Inline SVG charts for the public site, built from the estimates bundle.

Two charts (design document, owner decision 2026-10-28): the first-year effect under each
comparison on the home page, and CBD actual against its synthetic control on each outcome
page. No chart library and no third-party script: the SVG is written here, and a short
inline script adds the hover readout. Every value a reader can hover is also in a table
under the chart, so nothing depends on the pointer. The charts render from the bundle
alone, so archived releases get them too.
"""

from __future__ import annotations

import datetime as dt
import html
import json
import math
from typing import Any

from roundtrip.report.summary import DESIGN_LABELS

# Series colours, validated (dataviz six checks) on the light panel #ffffff and the dark
# panel #1c1f23: light slots 1-2, dark steps of the same hues.
CHART_CSS = """
:root { --c1: #2a78d6; --c2: #eb6834; --grid: #e6e5df; --axis: #c3c2b7; }
@media (prefers-color-scheme: dark) {
  :root { --c1: #3987e5; --c2: #d95926; --grid: #2c2f34; --axis: #4a4d53; }
}
.viz { position: relative; }
.viz svg { display: block; width: 100%; height: auto; min-width: 26rem;
           font: 12px system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
.viz .scroll { overflow-x: auto; }
.viz .grid { stroke: var(--grid); stroke-width: 1; }
.viz .axis { stroke: var(--axis); stroke-width: 1; }
.viz .tick { fill: var(--muted); }
.viz .lab { fill: var(--fg); }
.viz .group { fill: var(--fg); font-weight: 600; }
.viz .zero { stroke: var(--axis); stroke-width: 1; }
.viz .window { fill: var(--fg); fill-opacity: 0.05; }
.viz .s1 { stroke: var(--c1); fill: none; stroke-width: 2; stroke-linejoin: round;
           stroke-linecap: round; }
.viz .s2 { stroke: var(--c2); fill: none; stroke-width: 2; stroke-linejoin: round;
           stroke-linecap: round; }
.viz .mark .bar { stroke: var(--c1); stroke-width: 2; stroke-linecap: round; }
.viz .mark .dot { fill: var(--c1); stroke: var(--panel); stroke-width: 2; }
.viz .mark .hit { fill: transparent; }
.viz .mark:hover .dot, .viz .mark.on .dot { r: 6; }
.viz .mark:focus { outline: none; }
.viz .mark:focus-visible .dot { stroke: var(--accent); }
.viz svg:focus { outline: none; }
.viz svg:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.viz .cross { stroke: var(--muted); stroke-width: 1; opacity: 0; }
.viz .cross-dot { stroke: var(--panel); stroke-width: 2; opacity: 0; }
.viz .cross-dot.d1 { fill: var(--c1); }
.viz .cross-dot.d2 { fill: var(--c2); }
.viz.live .cross, .viz.live .cross-dot { opacity: 1; }
.viz .tip { position: absolute; pointer-events: none; display: none; z-index: 2;
            background: var(--card); color: var(--fg); border: 1px solid var(--rule);
            border-radius: 6px; padding: 0.4rem 0.6rem; font-size: 0.8rem; line-height: 1.4;
            box-shadow: 0 2px 8px rgba(0,0,0,0.12); white-space: nowrap;
            font-variant-numeric: tabular-nums; }
.viz .tip b { font-weight: 600; }
.viz .tip .key { display: inline-block; width: 0.9rem; height: 0; border-top: 2px solid;
                 vertical-align: middle; margin-right: 0.4rem; }
.viz .tip .k1 { border-color: var(--c1); } .viz .tip .k2 { border-color: var(--c2); }
.viz .tip .muted { color: var(--muted); }
.legend { display: flex; flex-wrap: wrap; gap: 0.3rem 1.2rem; font-size: 0.85rem;
          color: var(--muted); margin: 0.3rem 0.2rem 0; }
.legend .k1::before, .legend .k2::before { content: ""; display: inline-block; width: 1rem;
                       height: 0; border-top: 2px solid var(--c1); vertical-align: middle;
                       margin-right: 0.4rem; }
.legend .k2::before { border-color: var(--c2); }
.legend .unit { margin-left: auto; }
.viz-table summary { cursor: pointer; color: var(--muted); font-size: 0.85rem; }
"""

# One script for both chart kinds. Values and labels are read from data attributes and
# inserted with textContent, never as HTML.
CHART_JS = """
(function () {
  // keep chart text near 12 CSS px whatever the rendered width (the SVG scales with
  // its viewBox); without script it stays at 12 user units
  function fit() {
    document.querySelectorAll('.viz svg').forEach(function (svg) {
      var r = svg.getBoundingClientRect(), vb = svg.viewBox.baseVal;
      if (r.width > 0) svg.style.fontSize = Math.min(16, 12 * vb.width / r.width) + 'px';
    });
  }
  fit(); window.addEventListener('resize', fit);
  function tip(viz) { var t = viz.querySelector('.tip'); return t; }
  function place(viz, t, x, y) {
    var r = viz.getBoundingClientRect();
    t.style.display = 'block';
    var w = t.offsetWidth, h = t.offsetHeight;
    var left = x - r.left + 14, top = y - r.top - h - 10;
    if (left + w > r.width - 4) left = x - r.left - w - 14;
    if (left < 4) left = 4;
    if (top < 4) top = y - r.top + 14;
    t.style.left = left + 'px'; t.style.top = top + 'px';
  }
  function row(t, cls, value, label) {
    var d = document.createElement('div');
    if (cls) {
      var k = document.createElement('span'); k.className = 'key ' + cls; d.appendChild(k);
    }
    var b = document.createElement('b'); b.textContent = value; d.appendChild(b);
    var s = document.createElement('span'); s.className = 'muted'; s.textContent = ' ' + label;
    d.appendChild(s); t.appendChild(d);
  }
  document.querySelectorAll('.viz-dots').forEach(function (viz) {
    var t = tip(viz);
    viz.querySelectorAll('.mark').forEach(function (m) {
      function show(ev) {
        t.textContent = '';
        row(t, '', m.dataset.effect, m.dataset.label);
        row(t, '', m.dataset.interval, '95% interval');
        viz.querySelectorAll('.mark.on').forEach(function (o) { o.classList.remove('on'); });
        m.classList.add('on');
        var box = m.querySelector('.dot').getBoundingClientRect();
        place(viz, t, ev && ev.clientX ? ev.clientX : box.left + box.width / 2,
              box.top + box.height / 2);
      }
      function hide() { t.style.display = 'none'; m.classList.remove('on'); }
      m.addEventListener('pointerenter', show); m.addEventListener('pointermove', show);
      m.addEventListener('pointerleave', hide);
      m.addEventListener('focus', show); m.addEventListener('blur', hide);
    });
  });
  document.querySelectorAll('.viz-lines').forEach(function (viz) {
    var svg = viz.querySelector('svg'), t = tip(viz);
    var data = JSON.parse(viz.querySelector('script[type="application/json"]').textContent);
    var xs = data.x, cross = svg.querySelector('.cross');
    var d1 = svg.querySelector('.cross-dot.d1'), d2 = svg.querySelector('.cross-dot.d2');
    var i = -1;
    function at(k, ev) {
      if (k < 0 || k >= xs.length) return;
      i = k; viz.classList.add('live');
      cross.setAttribute('x1', xs[k]); cross.setAttribute('x2', xs[k]);
      d1.setAttribute('cx', xs[k]); d1.setAttribute('cy', data.y1[k]);
      d2.setAttribute('cx', xs[k]); d2.setAttribute('cy', data.y2[k]);
      t.textContent = '';
      var head = document.createElement('div'); head.className = 'muted';
      head.textContent = data.labels[k]; t.appendChild(head);
      row(t, 'k1', data.v1[k], data.names[0]);
      row(t, 'k2', data.v2[k], data.names[1]);
      var r = svg.getBoundingClientRect(), vb = svg.viewBox.baseVal;
      var px = r.left + xs[k] * r.width / vb.width;
      var py = ev && ev.clientY ? ev.clientY : r.top + data.y1[k] * r.height / vb.height;
      place(viz, t, px, py);
    }
    function nearest(clientX) {
      var r = svg.getBoundingClientRect(), vb = svg.viewBox.baseVal;
      var x = (clientX - r.left) * vb.width / r.width, best = 0, bd = Infinity;
      for (var k = 0; k < xs.length; k++) {
        var d = Math.abs(xs[k] - x); if (d < bd) { bd = d; best = k; }
      }
      return best;
    }
    function hide() { viz.classList.remove('live'); t.style.display = 'none'; }
    svg.addEventListener('pointermove', function (ev) { at(nearest(ev.clientX), ev); });
    svg.addEventListener('pointerleave', hide);
    svg.addEventListener('focus', function () { at(i < 0 ? xs.length - 1 : i); });
    svg.addEventListener('blur', hide);
    svg.addEventListener('keydown', function (ev) {
      if (ev.key === 'ArrowLeft') {
        at(Math.max(0, (i < 0 ? xs.length : i) - 1)); ev.preventDefault();
      }
      if (ev.key === 'ArrowRight') {
        at(Math.min(xs.length - 1, i + 1)); ev.preventDefault();
      }
    });
  });
})();
"""

OUTCOME_UNITS = {
    "n_trips": ("pickups per day", "{:,.0f}"),
    "mean_wait_seconds": ("seconds", "{:.0f} s"),
    "mean_base_fare": ("dollars", "${:.2f}"),
}


def _f(x: float) -> str:
    """Compact coordinate for the SVG."""
    return f"{x:.1f}".rstrip("0").rstrip(".")


def _nice_ticks(lo: float, hi: float, n: int = 4) -> list[float]:
    """Round tick values spanning [lo, hi]."""
    if hi <= lo:
        hi = lo + 1
    raw = (hi - lo) / n
    mag = 10 ** math.floor(math.log10(raw))
    step = next(s * mag for s in (1, 2, 2.5, 5, 10) if s * mag >= raw)
    t = math.floor(lo / step) * step
    ticks = []
    while t < hi:  # the last tick is the first one at or above hi, so nothing is clipped
        ticks.append(round(t, 10))
        t += step
    ticks.append(round(t, 10))
    return ticks


def _signed(t: float) -> str:
    return "0%" if t == 0 else f"{t:+g}%"


def _compact(v: float) -> str:
    a = abs(v)
    if a >= 1_000_000:
        return f"{v / 1_000_000:g}M"
    if a >= 1_000:
        return f"{v / 1_000:g}K"
    return f"{v:g}"


# --- chart 1: first-year effect under each comparison ----------------------------------


def effect_chart(groups: list[tuple[str, list[dict[str, Any]]]]) -> str:
    """Dot-and-interval chart: one row per outcome and comparison, effect in percent with
    its 95% interval, zero line. ``groups`` pairs an outcome title with its post-window
    estimate rows (one per design)."""
    groups = [
        (title, [r for r in rows if r.get("effect_pct") is not None]) for title, rows in groups
    ]
    groups = [(t, rows) for t, rows in groups if rows]
    if not groups:
        return ""
    vals = [float(v) for _, rows in groups for r in rows for v in (r["lo_pct"], r["hi_pct"])]
    lo, hi = min([*vals, 0.0]), max([*vals, 0.0])
    pad = max(1.0, (hi - lo) * 0.08)
    ticks = _nice_ticks(lo - pad, hi + pad, 5)
    x0, x1 = ticks[0], ticks[-1]

    left, right, top = 150, 24, 14
    width = 720
    plot_w = width - left - right
    row_h, gap = 22, 14

    def xpos(v: float) -> float:
        return left + (v - x0) / (x1 - x0) * plot_w

    y = top
    body: list[str] = []
    marks: list[str] = []
    table: list[str] = []
    for title, rows in groups:
        y += 16
        body.append(
            f'<text class="group" x="{left - 8}" y="{_f(y)}" text-anchor="end">'
            f"{html.escape(title)}</text>"
        )
        y += 6
        for r in rows:
            cy = y + row_h / 2
            label = DESIGN_LABELS.get(r["design"], r["design"])
            e, lo_, hi_ = float(r["effect_pct"]), float(r["lo_pct"]), float(r["hi_pct"])
            body.append(
                f'<text class="lab" x="{left - 8}" y="{_f(cy + 4)}" text-anchor="end">'
                f"{html.escape(label)}</text>"
            )
            marks.append(
                f'<g class="mark" tabindex="0" data-label="{html.escape(title)}, '
                f'{html.escape(label)}" data-effect="{e:+.1f}%" '
                f'data-interval="{lo_:+.1f}% to {hi_:+.1f}%" role="img" '
                f'aria-label="{html.escape(title)}, {html.escape(label)}: {e:+.1f}%, '
                f'95% interval {lo_:+.1f} to {hi_:+.1f}">'
                f'<rect class="hit" x="{_f(xpos(lo_) - 6)}" y="{_f(cy - row_h / 2)}" '
                f'width="{_f(xpos(hi_) - xpos(lo_) + 12)}" height="{row_h}"/>'
                f'<line class="bar" x1="{_f(xpos(lo_))}" x2="{_f(xpos(hi_))}" '
                f'y1="{_f(cy)}" y2="{_f(cy)}"/>'
                f'<circle class="dot" cx="{_f(xpos(e))}" cy="{_f(cy)}" r="4.5"/></g>'
            )
            table.append(
                f"<tr><td>{html.escape(title)}</td><td>{html.escape(label)}</td>"
                f"<td>{e:+.1f}%</td><td>[{lo_:+.1f}, {hi_:+.1f}]</td></tr>"
            )
            y += row_h
        y += gap
    bottom = y + 22
    height = bottom + 4
    grid = [
        f'<line class="{"zero" if t == 0 else "grid"}" x1="{_f(xpos(t))}" x2="{_f(xpos(t))}" '
        f'y1="{top}" y2="{_f(y)}"/>'
        f'<text class="tick" x="{_f(xpos(t))}" y="{_f(y + 16)}" text-anchor="middle">'
        f"{_signed(t)}</text>"
        for t in ticks
    ]
    svg = (
        f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="First-year effect '
        'under the three comparisons, with 95% intervals">'
        + "".join(grid)
        + "".join(body)
        + "".join(marks)
        + "</svg>"
    )
    return (
        '<figure><div class="panel viz viz-dots"><div class="scroll">'
        + svg
        + '</div><div class="tip"></div></div>'
        "<figcaption>First-year effect on the CBD under the three comparisons, in percent, "
        "with 95% intervals. Each is net of what happened in comparable zones outside the "
        "CBD. Hover or tab to a point for the numbers.</figcaption>"
        '<details class="viz-table"><summary>The numbers in this chart</summary>'
        '<div class="table-wrap"><table><thead><tr><th>Outcome</th><th>Comparison</th>'
        "<th>Effect</th><th>95% interval</th></tr></thead><tbody>"
        + "".join(table)
        + "</tbody></table></div></details></figure>"
    )


# --- chart 2: actual against the synthetic control -------------------------------------


def weekly(series: dict[str, list[Any]]) -> list[dict[str, Any]]:
    """Seven-day means of the daily actual and synthetic series, labelled by the first
    day of each block; a trailing block shorter than four days is dropped."""
    days, act, syn = series.get("day", []), series.get("actual", []), series.get("synthetic", [])
    out: list[dict[str, Any]] = []
    for i in range(0, len(days), 7):
        a = [float(v) for v in act[i : i + 7] if v is not None]
        s = [float(v) for v in syn[i : i + 7] if v is not None]
        if len(a) < 4 or len(s) < 4:
            continue
        out.append({"week": str(days[i]), "actual": sum(a) / len(a), "synthetic": sum(s) / len(s)})
    return out


def series_chart(bundle: dict[str, Any], outcome: str, title: str) -> str:
    """Weekly CBD actual and synthetic control with the anticipation window and the first
    year of the charge shaded, a crosshair readout, and the weekly table underneath."""
    block = (bundle.get("synth") or {}).get(outcome) or {}
    weeks = weekly(block.get("series") or {})
    if len(weeks) < 2:
        return ""
    unit, fmt = OUTCOME_UNITS.get(outcome, ("", "{:.1f}"))
    windows = bundle.get("windows", {})

    width, height = 720, 300
    left, right, top, bottom = 56, 16, 12, 34
    plot_w, plot_h = width - left - right, height - top - bottom

    def day(s: str) -> dt.date:
        return dt.date.fromisoformat(s)

    t0, t1 = day(weeks[0]["week"]), day(weeks[-1]["week"]) + dt.timedelta(days=6)
    span = max(1, (t1 - t0).days)

    def xpos(d: dt.date) -> float:
        return left + (d - t0).days / span * plot_w

    vals = [w["actual"] for w in weeks] + [w["synthetic"] for w in weeks]
    lo, hi = min(vals), max(vals)
    pad = (hi - lo) * 0.08 or 1.0
    ticks = _nice_ticks(lo - pad, hi + pad, 4)
    y0, y1 = ticks[0], ticks[-1]

    def ypos(v: float) -> float:
        return top + (y1 - v) / (y1 - y0) * plot_h

    parts: list[str] = [
        f'<svg viewBox="0 0 {width} {height}" role="img" tabindex="0" aria-label="'
        f'{html.escape(title)}: CBD actual and synthetic control, weekly means">'
    ]
    # shaded windows
    for name in ("anticipation", "post"):
        w = windows.get(name) or {}
        if "start" in w and "end" in w:
            a, b = max(day(w["start"]), t0), min(day(w["end"]), t1)
            if b > a:
                parts.append(
                    f'<rect class="window" x="{_f(xpos(a))}" y="{top}" '
                    f'width="{_f(xpos(b) - xpos(a))}" height="{plot_h}"/>'
                )
                # the anticipation window is narrow: its label sits to the left of it
                if name == "anticipation":
                    parts.append(
                        f'<text class="tick" x="{_f(xpos(a) - 4)}" y="{top + 12}" '
                        'text-anchor="end">anticipation</text>'
                    )
                else:
                    parts.append(
                        f'<text class="tick" x="{_f(xpos(a) + 4)}" y="{top + 12}">'
                        "first year of the charge</text>"
                    )
    # y grid and ticks
    for t in ticks:
        parts.append(
            f'<line class="grid" x1="{left}" x2="{width - right}" y1="{_f(ypos(t))}" '
            f'y2="{_f(ypos(t))}"/>'
            f'<text class="tick" x="{left - 8}" y="{_f(ypos(t) + 4)}" text-anchor="end">'
            f"{_compact(t)}</text>"
        )
    # x ticks: each January
    for year in range(t0.year, t1.year + 1):
        d = dt.date(year, 1, 1)
        if t0 <= d <= t1:
            parts.append(
                f'<line class="axis" x1="{_f(xpos(d))}" x2="{_f(xpos(d))}" y1="{top + plot_h}" '
                f'y2="{top + plot_h + 5}"/>'
                f'<text class="tick" x="{_f(xpos(d))}" y="{top + plot_h + 20}" '
                f'text-anchor="middle">{year}</text>'
            )
    parts.append(
        f'<line class="axis" x1="{left}" x2="{width - right}" y1="{top + plot_h}" '
        f'y2="{top + plot_h}"/>'
    )
    xs = [xpos(day(w["week"]) + dt.timedelta(days=3)) for w in weeks]
    ya = [ypos(w["actual"]) for w in weeks]
    ys = [ypos(w["synthetic"]) for w in weeks]
    parts.append(
        '<polyline class="s2" points="'
        + " ".join(f"{_f(x)},{_f(y)}" for x, y in zip(xs, ys, strict=True))
        + '"/>'
    )
    parts.append(
        '<polyline class="s1" points="'
        + " ".join(f"{_f(x)},{_f(y)}" for x, y in zip(xs, ya, strict=True))
        + '"/>'
    )
    parts.append(
        f'<line class="cross" x1="0" x2="0" y1="{top}" y2="{top + plot_h}"/>'
        '<circle class="cross-dot d1" r="4.5"/><circle class="cross-dot d2" r="4.5"/></svg>'
    )
    data = {
        "x": [round(x, 1) for x in xs],
        "y1": [round(y, 1) for y in ya],
        "y2": [round(y, 1) for y in ys],
        "v1": [fmt.format(w["actual"]) for w in weeks],
        "v2": [fmt.format(w["synthetic"]) for w in weeks],
        "labels": [f"week of {w['week']}" for w in weeks],
        "names": ["CBD actual", "synthetic control"],
    }
    table = "".join(
        f"<tr><td>{html.escape(w['week'])}</td><td>{fmt.format(w['actual'])}</td>"
        f"<td>{fmt.format(w['synthetic'])}</td></tr>"
        for w in weeks
    )
    return (
        '<figure><div class="panel viz viz-lines"><div class="scroll">'
        + "".join(parts)
        + "</div>"
        + '<script type="application/json">'
        + json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
        + "</script>"
        + '<div class="tip"></div></div>'
        '<p class="legend"><span class="k1">CBD actual</span>'
        '<span class="k2">synthetic control</span>'
        f'<span class="unit">{html.escape(unit)}, weekly means</span></p>'
        "<figcaption>CBD actual and its synthetic control, weekly means. The anticipation "
        "window and the first year of the charge are shaded. Hover, or focus the chart and "
        "use the arrow keys, to read a week.</figcaption>"
        '<details class="viz-table"><summary>The numbers in this chart</summary>'
        '<div class="table-wrap"><table><thead><tr><th>Week of</th><th>CBD actual</th>'
        "<th>Synthetic control</th></tr></thead><tbody>" + table + "</tbody></table></div>"
        "</details></figure>"
    )
