"""Human review gate (brief section 7).

- ``prepare``: create ``reviews/YYYY-MM.md`` from the template with the candidate run's
  identifiers filled in.
- ``check``: fail if the record is missing or any field is unfilled (the CI check).
- ``approve`` / ``reject``: append to ``releases/index.json`` (git SHA, manifest hash,
  reviewer, date, decision, reason) and, on approval, copy the estimates bundle into
  ``releases/<month>/`` so the next candidate can be diffed against it.

Approval itself happens through the GitHub ``release`` environment; ``approve`` is what the
publish workflow runs after that approval.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from roundtrip.config import Settings, validate_month

log = logging.getLogger(__name__)

FILL = "_fill_"
DECISIONS = ("approve", "reject", "approve with notes")
REQUIRED_SECTIONS = (
    "Data checks acknowledged",
    "Estimate changes versus last approved release",
    "Placebo results acceptable",
    "Model drift decision",
    "Known issues",
    "Decision",
    "Reviewer",
)


def review_path(settings: Settings, month: str) -> Path:
    return settings.reviews_dir / f"{validate_month(month)}.md"


def index_path(settings: Settings) -> Path:
    return settings.releases_dir / "index.json"


def load_index(settings: Settings) -> dict[str, list[dict[str, Any]]]:
    p = index_path(settings)
    data: dict[str, list[dict[str, Any]]] = (
        json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    )
    data.setdefault("approved", [])
    data.setdefault("rejected", [])
    return data


def save_index(settings: Settings, data: dict[str, list[dict[str, Any]]]) -> None:
    p = index_path(settings)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def last_approved(settings: Settings) -> dict[str, Any] | None:
    approved = load_index(settings)["approved"]
    return max(approved, key=lambda e: str(e.get("month", ""))) if approved else None


def last_approved_bundle(settings: Settings) -> Path | None:
    entry = last_approved(settings)
    if entry is None:
        return None
    p = settings.releases_dir / str(entry["month"]) / "estimates.json"
    return p if p.exists() else None


def _bundle_ids(bundle_path: Path | None) -> dict[str, str]:
    if bundle_path is None or not bundle_path.exists():
        return {"run_id": "n/a", "git_sha": "n/a", "manifest_hash": "n/a", "data_end": "n/a"}
    b = json.loads(bundle_path.read_text(encoding="utf-8"))
    return {
        "run_id": str(b.get("run_id")),
        "git_sha": str(b.get("git_sha"))[:12],
        "manifest_hash": str(b.get("manifest_hash"))[:12],
        "data_end": str(b.get("data_end")),
    }


def prepare(
    settings: Settings, month: str, bundle_path: Path | None = None, force: bool = False
) -> Path:
    """Write the review record from the template. Existing records are kept unless
    ``force``, so a reviewer's partial notes are never overwritten by a re-run."""
    path = review_path(settings, month)
    if path.exists() and not force:
        log.info("%s exists; not overwritten", path)
        return path
    template = (settings.reviews_dir / "TEMPLATE.md").read_text(encoding="utf-8")
    text = template.replace("{month}", validate_month(month))
    for k, v in _bundle_ids(bundle_path).items():
        text = text.replace("{" + k + "}", v)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def parse(path: Path) -> dict[str, str]:
    """Section heading -> body text (stripped), for the '## ' sections."""
    text = path.read_text(encoding="utf-8")
    sections: dict[str, str] = {}
    current: str | None = None
    buf: list[str] = []
    for line in text.splitlines():
        if line.startswith("## "):
            if current is not None:
                sections[current] = "\n".join(buf).strip()
            current, buf = line[3:].strip(), []
        elif current is not None:
            buf.append(line)
    if current is not None:
        sections[current] = "\n".join(buf).strip()
    return sections


def reviewed_run_id(path: Path) -> str | None:
    """The run id named in the record's header ("Candidate run `<id>`")."""
    m = re.search(r"Candidate run `([^`]+)`", path.read_text(encoding="utf-8"))
    return m.group(1) if m else None


def decision_of(sections: dict[str, str]) -> str | None:
    body = sections.get("Decision", "").lower()
    found = [d for d in DECISIONS if re.search(rf"\b{re.escape(d)}\b", body)]
    if not found:
        return None
    # "approve with notes" contains "approve"; prefer the longer match
    return max(found, key=len)


def check(path: Path) -> list[str]:
    """Problems with a review record; empty means it passes."""
    if not path.exists():
        return [f"{path} is missing (run `roundtrip release prepare`)"]
    problems: list[str] = []
    sections = parse(path)
    for name in REQUIRED_SECTIONS:
        if name not in sections:
            problems.append(f"section '{name}' is missing")
        elif not sections[name] or FILL in sections[name]:
            problems.append(f"section '{name}' is not filled in")
    if "Decision" in sections and decision_of(sections) is None:
        problems.append("Decision must contain one of: " + ", ".join(DECISIONS))
    reviewer = sections.get("Reviewer", "")
    m_init = re.search(r"Initials:\s*(\S+)", reviewer)
    if not m_init or m_init.group(1) == FILL:
        problems.append("Reviewer initials are missing")
    m_date = re.search(r"Date:\s*(\d{4}-\d{2}-\d{2})", reviewer)
    if not m_date:
        problems.append("Reviewer date must be YYYY-MM-DD")
    else:
        try:
            dt.date.fromisoformat(m_date.group(1))
        except ValueError:
            problems.append("Reviewer date is not a valid date")
    return problems


def _reviewer(sections: dict[str, str]) -> tuple[str, str]:
    r = sections.get("Reviewer", "")
    init = re.search(r"Initials:\s*(\S+)", r)
    date = re.search(r"Date:\s*(\d{4}-\d{2}-\d{2})", r)
    return (init.group(1) if init else "?", date.group(1) if date else "?")


def record(
    settings: Settings,
    month: str,
    status: str,
    reviewer: str,
    date: str,
    bundle_path: Path | None = None,
    reason: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    ids = _bundle_ids(bundle_path)
    entry: dict[str, Any] = {
        "month": validate_month(month),
        "status": status,
        "run_id": ids["run_id"],
        "git_sha": ids["git_sha"],
        "manifest_hash": ids["manifest_hash"],
        "data_end": ids["data_end"],
        "reviewer": reviewer,
        "date": date,
        "recorded_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
    }
    if reason:
        entry["reason"] = reason
    if notes:
        entry["notes"] = notes
    index = load_index(settings)
    key = "approved" if status.startswith("approve") else "rejected"
    index[key] = [e for e in index[key] if e.get("month") != entry["month"]] + [entry]
    save_index(settings, index)
    if key == "approved" and bundle_path is not None and bundle_path.exists():
        dest = settings.releases_dir / entry["month"]
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copy2(bundle_path, dest / "estimates.json")
    return entry


def approve(settings: Settings, month: str, bundle_path: Path) -> dict[str, Any]:
    """Record an approval from a complete review record whose decision is approve."""
    path = review_path(settings, month)
    problems = check(path)
    if problems:
        msg = "review record incomplete: " + "; ".join(problems)
        raise ValueError(msg)
    reviewed = reviewed_run_id(path)
    bundled = _bundle_ids(bundle_path)["run_id"]
    if reviewed is not None and reviewed != bundled:
        msg = (
            f"review record is for run {reviewed} but the bundle is run {bundled}; "
            "approve only the run that was reviewed"
        )
        raise ValueError(msg)
    sections = parse(path)
    decision = decision_of(sections)
    if decision is None or not decision.startswith("approve"):
        msg = f"review record decision is '{decision}', not approve"
        raise ValueError(msg)
    initials, date = _reviewer(sections)
    return record(
        settings, month, decision, initials, date, bundle_path, notes=sections.get("Known issues")
    )


def reject(
    settings: Settings,
    month: str,
    reason: str,
    bundle_path: Path | None = None,
    close_pr: bool = True,
) -> dict[str, Any]:
    """Record a rejection and, when ``gh`` is available, close the candidate PR."""
    path = review_path(settings, month)
    initials, date = _reviewer(parse(path)) if path.exists() else ("?", "?")
    if date == "?":
        date = dt.date.today().isoformat()
    entry = record(settings, month, "rejected", initials, date, bundle_path, reason=reason)
    if close_pr:
        branch = f"candidate/{validate_month(month)}"
        try:
            subprocess.run(
                ["gh", "pr", "close", branch, "--comment", f"Rejected by reviewer: {reason}"],
                check=False,
                capture_output=True,
                text=True,
                timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:  # pragma: no cover
            log.warning("could not close PR for %s: %s", branch, exc)
    return entry
