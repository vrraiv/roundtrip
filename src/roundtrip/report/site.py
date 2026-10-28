"""Static site from ``REPORTS/*.md`` (plain Markdown to HTML; no server, no framework)."""

from __future__ import annotations

import html
import shutil
from pathlib import Path

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


def build_site(reports_dir: Path, out_dir: Path) -> list[Path]:
    """Render every ``*.md`` in ``reports_dir`` to ``out_dir`` and copy figures."""
    out_dir.mkdir(parents=True, exist_ok=True)
    pages = sorted(reports_dir.glob("*.md"))
    nav = " ".join(
        f'<a href="{p.stem}.html">{html.escape(_title(p.read_text(encoding="utf-8"), p.stem))}</a>'
        for p in pages
    )
    written: list[Path] = []
    for p in pages:
        text = p.read_text(encoding="utf-8")
        body = markdown.markdown(text, extensions=["tables", "fenced_code"])
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
