"""Review records written from the issue form (review.yml), and the candidate banner."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from roundtrip import release
from roundtrip.cli import app
from roundtrip.config import Settings, get_settings
from roundtrip.report.build import build_report
from roundtrip.report.site import approval_banner, build_site
from tests.test_release import _bundle, repo_settings  # noqa: F401

FORM_BODY = """### Month

2025-06

### Run id

r1

### Decision

approve with notes

### Data checks acknowledged

yes

### Estimate changes versus last approved release

first release

### Placebo results acceptable

no

### Model drift decision

investigate

### Known issues

the synthetic control may need a redesign

### Notes

_No response_
"""


def test_parse_form_maps_labels_and_empty_answers() -> None:
    fields = release.parse_form(FORM_BODY.replace("\n", "\r\n"))
    assert fields["Month"] == "2025-06"
    assert fields["Decision"] == "approve with notes"
    assert fields["Known issues"] == "the synthetic control may need a redesign"
    assert fields["Notes"] == ""


def test_from_form_writes_a_record_that_approves(
    repo_settings: Settings,  # noqa: F811
    tmp_path: Path,
) -> None:
    bundle = _bundle(tmp_path / "est" / "estimates.json", run_id="r1")
    # a stale hand-written record is replaced, not kept
    release.prepare(repo_settings, "2025-06", bundle)
    path, problems = release.from_form(
        repo_settings, release.parse_form(FORM_BODY), "vrraiv", "2026-01-02", bundle
    )
    assert problems == []
    text = path.read_text(encoding="utf-8")
    assert "Candidate run `r1`" in text
    assert "git `abcdef123456`" in text
    assert "## Notes" not in text
    sections = release.parse(path)
    assert sections["Data checks acknowledged"] == "yes"
    assert sections["Known issues"] == "the synthetic control may need a redesign"
    assert release.decision_of(sections) == "approve with notes"
    entry = release.approve(repo_settings, "2025-06", bundle)
    assert entry["reviewer"] == "vrraiv"
    assert entry["date"] == "2026-01-02"
    assert entry["notes"] == "the synthetic control may need a redesign"


def test_from_form_keeps_notes_and_reports_missing_answers(
    repo_settings: Settings,  # noqa: F811
    tmp_path: Path,
) -> None:
    bundle = _bundle(tmp_path / "est" / "estimates.json", run_id="r1")
    body = FORM_BODY.replace("_No response_", "placebo p too small").replace(
        "### Known issues\n\nthe synthetic control may need a redesign\n", ""
    )
    path, problems = release.from_form(
        repo_settings, release.parse_form(body), "vrraiv", "2026-01-02", bundle
    )
    assert release.parse(path)["Notes"] == "placebo p too small"
    assert problems == ["section 'Known issues' is not filled in"]


def test_from_form_refuses_another_run(
    repo_settings: Settings,  # noqa: F811
    tmp_path: Path,
) -> None:
    newer = _bundle(tmp_path / "est" / "estimates.json", run_id="r2")
    with pytest.raises(ValueError, match="reviews run r1 but the candidate is run r2"):
        release.from_form(
            repo_settings, release.parse_form(FORM_BODY), "vrraiv", "2026-01-02", newer
        )
    assert not release.review_path(repo_settings, "2025-06").exists()


def test_cli_from_issue(
    repo_settings: Settings,  # noqa: F811
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ROUNDTRIP_SQL_DIR", str(repo_settings.sql_dir))
    get_settings.cache_clear()
    bundle = _bundle(tmp_path / "est" / "estimates.json", run_id="r1")
    body = tmp_path / "body.md"
    body.write_text(FORM_BODY.replace("approve with notes", "reject"), encoding="utf-8")
    args = ["release", "from-issue", "--body-file", str(body), "--reviewer", "vrraiv"]
    args += ["--date", "2026-01-02", "--bundle", str(bundle)]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.output
    assert '"decision": "reject"' in result.stdout
    assert '"reason": "the synthetic control may need a redesign"' in result.stdout
    assert release.review_path(repo_settings, "2025-06").exists()

    body.write_text(FORM_BODY.replace("r1", "r9"), encoding="utf-8")
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 1
    assert "reviews run r9" in result.output


def test_site_banner_marks_a_newer_candidate(
    repo_settings: Settings,  # noqa: F811
    tmp_path: Path,
) -> None:
    approved = _bundle(tmp_path / "est" / "estimates.json", run_id="r1")
    release.from_form(
        repo_settings, release.parse_form(FORM_BODY), "vrraiv", "2026-01-02", approved
    )
    release.approve(repo_settings, "2025-06", approved)
    candidate = _bundle(tmp_path / "est2" / "estimates.json", run_id="r2", data_end="2025-07-31")
    out = tmp_path / "REPORTS"
    build_report(repo_settings, candidate, approved, out_dir=out)
    site = tmp_path / "site"
    build_site(out, site, repo_settings.releases_dir)
    page = (site / "index.html").read_text(encoding="utf-8")
    assert "Not yet reviewed" in page
    assert "Candidate run <code>r2</code>" in page
    assert "last approved release is 2025-06" in page
    assert "Approved by vrraiv" not in page
    # the same run as the approval gets the approval banner
    assert "Approved by vrraiv" in approval_banner(release.last_approved(repo_settings), "r1")
