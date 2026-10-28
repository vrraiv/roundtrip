"""Static site from ``REPORTS/*.md`` (plain Markdown to HTML; no server, no framework)."""

from __future__ import annotations

import html
import json
import shutil
from pathlib import Path
from typing import Any

import markdown

CSS = """
:root { color-scheme: light dark; }
body { max-width: 56rem; margin: 2rem auto; padding: 0 1rem;
       font: 16px/1.55 system-ui, sans-serif; }
img { max-width: 100%; height: auto; }
table { border-collapse: collapse; font-size: 0.9em; margin: 1rem 0;
        overflow-x: auto; display: block; }
th, td { border: 1px solid #8884; padding: 0.3rem 0.6rem; text-align: left; }
code { font-size: 0.9em; }
nav a { margin-right: 1rem; }
footer { margin-top: 3rem; font-size: 0.85em; opacity: 0.7; }
"""

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title><style>{css}</style></head>
<body><nav>{nav}</nav>
{body}
<footer>Built from <code>REPORTS/</code> by the roundtrip pipeline. Estimates are candidates until
an economist approves them; see <code>releases/index.json</code> in the repository.</footer>
</body></html>
"""


def _title(md_text: str, fallback: str) -> str:
    for line in md_text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


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


def bundle_run_id(reports_dir: Path) -> str | None:
    p = reports_dir / "estimates.json"
    if not p.exists():
        return None
    run = json.loads(p.read_text(encoding="utf-8")).get("run_id")
    return None if run is None else str(run)


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


def build_site(reports_dir: Path, out_dir: Path, releases_dir: Path | None = None) -> list[Path]:
    """Render every ``*.md`` in ``reports_dir`` to ``out_dir`` and copy figures, with an
    approval banner from ``releases_dir/index.json`` on every page."""
    out_dir.mkdir(parents=True, exist_ok=True)
    banner = approval_banner(latest_approval(releases_dir), bundle_run_id(reports_dir))
    pages = sorted(reports_dir.glob("*.md"))
    nav = " ".join(
        f'<a href="{p.stem}.html">{html.escape(_title(p.read_text(encoding="utf-8"), p.stem))}</a>'
        for p in pages
    )
    written: list[Path] = []
    for p in pages:
        text = p.read_text(encoding="utf-8")
        body = markdown.markdown(banner + text, extensions=["tables", "fenced_code"])
        page = PAGE.format(title=html.escape(_title(text, p.stem)), css=CSS, nav=nav, body=body)
        dest = out_dir / f"{p.stem}.html"
        dest.write_text(page, encoding="utf-8")
        written.append(dest)
    if pages:
        index = out_dir / "index.html"
        shutil.copy2(written[0], index)
        written.append(index)
    figs = reports_dir / "figures"
    if figs.exists():
        (out_dir / "figures").mkdir(exist_ok=True)
        for f in figs.glob("*.png"):
            shutil.copy2(f, out_dir / "figures" / f.name)
    return written
