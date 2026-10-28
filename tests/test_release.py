"""Review gate, report builder and site builder."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from roundtrip import release
from roundtrip.config import Settings
from roundtrip.report.build import build_report
from roundtrip.report.site import build_site
from roundtrip.report.summary import bundle_summary, load_bundle

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def repo_settings(settings: Settings, tmp_path: Path) -> Settings:
    """Settings whose reviews/ and releases/ live in a temp copy, template included."""
    sql_dir = tmp_path / "repo" / "sql"
    sql_dir.mkdir(parents=True)
    reviews = tmp_path / "repo" / "reviews"
    reviews.mkdir()
    (reviews / "TEMPLATE.md").write_text(
        (REPO / "reviews" / "TEMPLATE.md").read_text(encoding="utf-8"), encoding="utf-8"
    )
    return settings.model_copy(update={"sql_dir": sql_dir})


def _bundle(path: Path, run_id: str = "r1", data_end: str = "2025-06-30") -> Path:
    b = {
        "run_id": run_id,
        "generated_at": "2026-01-01T00:00:00+00:00",
        "package_version": "0.1.0",
        "git_sha": "abcdef1234567890",
        "manifest_hash": "0123456789abcdef",
        "data_start": "2023-01-01",
        "data_end": data_end,
        "n_days": 10,
        "months": ["2023-01", "2025-06"],
        "design": {
            "treatment_date": "2025-01-05",
            "anticipation_start": "2024-11-14",
            "placebo_window": ["2023-11-14", "2024-01-05"],
            "pre_window_start": "2024-06-01",
            "exclude_airport_trips": False,
            "comparison_designs": ["prior_year", "synthetic_control", "pre_window"],
            "synth_outcomes": ["n_trips"],
            "placebo_dates": ["2024-03-03"],
            "event_weeks": [30, 30],
        },
        "groups": {"cbd": [1, 2], "manhattan_north": [3, 4], "inner_bk_queens": [5], "n_donors": 3},
        "windows": {
            "anticipation": {"start": "2024-11-14", "end": "2025-01-04"},
            "post": {"start": "2025-01-05", "end": "2025-06-30"},
        },
        "headline": {
            "n_trips": {
                "design": "synthetic_control",
                "window": "post",
                "outcome": "n_trips",
                "adjusted": True,
                "actual": 1.0,
                "counterfactual": 1.1,
                "effect": -0.1,
                "effect_pct": -9.1,
                "lo_pct": -12.0,
                "hi_pct": -6.0,
                "n_days": 177,
                "note": "",
            }
        },
        "estimates": [
            {
                "design": d,
                "window": w,
                "outcome": "n_trips",
                "adjusted": True,
                "actual": 1.0,
                "counterfactual": 1.1,
                "effect": -0.1,
                "effect_pct": -9.0 + i,
                "lo_pct": -12.0,
                "hi_pct": -6.0,
                "n_days": 50,
                "note": "",
            }
            for i, d in enumerate(["prior_year", "synthetic_control", "pre_window"])
            for w in ("anticipation", "post")
        ],
        "synth": {
            "n_trips": {
                "fit_start": "2023-01-01",
                "fit_end": "2024-11-13",
                "pre_rmspe": 1.0,
                "n_donors": 3,
                "weights": {"3 Z3": 0.6, "4 Z4": 0.4},
                "windows": {
                    "post": {
                        "n_days": 177,
                        "mean_actual": 1.0,
                        "mean_synthetic": 1.1,
                        "mean_gap": -0.1,
                        "mean_gap_pct": -9.1,
                        "rmspe": 0.1,
                        "rmspe_ratio": 2.0,
                    },
                },
                "placebo": {"post": {"window": "post", "p_value": 0.25, "treated_gap_pct": -9.1}},
                "test_2023": {
                    "windows": {"placebo_2023": {"mean_gap_pct": -1.0, "rmspe_ratio": 1.1}}
                },
                "series": {"day": [], "actual": [], "synthetic": []},
            }
        },
        "event_study": {
            "manhattan_north": {
                "n_trips": {
                    "transform": "log",
                    "pretrend_p": 0.3,
                    "anticipation_week": -8,
                    "static": {
                        "anticipation": {"estimate": -0.02, "lo": -0.05, "hi": 0.01, "p": 0.2},
                        "post": {"estimate": -0.09, "lo": -0.12, "hi": -0.06, "p": 0.0},
                    },
                    "dynamic": [
                        {"week": w, "estimate": 0.0, "lo": -0.1, "hi": 0.1, "se": 0.05, "p": 1.0}
                        for w in range(-3, 3)
                    ],
                }
            }
        },
        "placebos": [
            {
                "kind": "fake_date",
                "outcome": "n_trips",
                "control_group": "manhattan_north",
                "estimate": 0.01,
                "p": 0.5,
                "lo": -0.1,
                "hi": 0.1,
                "se": 0.05,
                "date": "2024-03-03",
            },
            {
                "kind": "fake_zones",
                "outcome": "n_trips",
                "control_group": "manhattan_north",
                "estimate": -0.02,
                "p": 0.3,
                "lo": -0.1,
                "hi": 0.1,
                "se": 0.05,
                "draw": 0,
                "n_fake": 1,
            },
        ],
        "elapsed_seconds": 1.0,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    (path.parent / "figures").mkdir(exist_ok=True)
    (path.parent / "figures" / "comparisons.png").write_bytes(b"png")
    path.write_text(json.dumps(b), encoding="utf-8")
    return path


def _fill(path: Path, decision: str = "approve") -> None:
    text = path.read_text(encoding="utf-8")
    text = text.replace(
        "_fill_ (yes/no; name any soft warnings you looked at)",
        "yes, saw the soft warning on nulls",
    )
    text = text.replace(
        '_fill_ (plausible? refer to the "What changed" table in the report)', "first release"
    )
    text = text.replace("_fill_ (yes/no and why)", "yes, placebos centred on zero")
    text = text.replace('_fill_ (keep / retrain / investigate; "n/a" before phase 3)', "n/a")
    text = text.replace("## Known issues\n\n_fill_", "## Known issues\n\nnone")
    text = text.replace("_fill_ (approve / reject / approve with notes)", decision)
    text = text.replace("Initials: _fill_", "Initials: VR")
    text = text.replace("Date: _fill_ (YYYY-MM-DD)", "Date: 2026-01-02")
    path.write_text(text, encoding="utf-8")


def test_prepare_and_check(repo_settings: Settings, tmp_path: Path) -> None:
    bundle = _bundle(tmp_path / "est" / "estimates.json")
    path = release.prepare(repo_settings, "2025-06", bundle)
    assert path.name == "2025-06.md"
    text = path.read_text(encoding="utf-8")
    assert "Review record: 2025-06" in text
    assert "`r1`" in text
    problems = release.check(path)
    assert any("not filled" in p for p in problems)
    assert any("initials" in p.lower() for p in problems)
    # a second prepare keeps the reviewer's file
    path.write_text(text + "\nnote", encoding="utf-8")
    release.prepare(repo_settings, "2025-06", bundle)
    assert path.read_text(encoding="utf-8").endswith("note")
    _fill(path)
    assert release.check(path) == []
    assert release.check(tmp_path / "missing.md")


def test_check_rejects_bad_decision_and_date(repo_settings: Settings, tmp_path: Path) -> None:
    bundle = _bundle(tmp_path / "est" / "estimates.json")
    path = release.prepare(repo_settings, "2025-06", bundle)
    _fill(path, decision="maybe later")
    problems = release.check(path)
    assert any("Decision must contain" in p for p in problems)
    text = path.read_text(encoding="utf-8").replace("Date: 2026-01-02", "Date: 2026-13-40")
    text = text.replace("maybe later", "approve with notes")
    path.write_text(text, encoding="utf-8")
    problems = release.check(path)
    assert any("valid date" in p for p in problems)


def test_approve_records_and_copies_bundle(repo_settings: Settings, tmp_path: Path) -> None:
    bundle = _bundle(tmp_path / "est" / "estimates.json")
    path = release.prepare(repo_settings, "2025-06", bundle)
    with pytest.raises(ValueError, match="incomplete"):
        release.approve(repo_settings, "2025-06", bundle)
    _fill(path, decision="approve with notes")
    entry = release.approve(repo_settings, "2025-06", bundle)
    assert entry["status"] == "approve with notes"
    assert entry["reviewer"] == "VR"
    assert entry["git_sha"] == "abcdef123456"
    index = release.load_index(repo_settings)
    assert [e["month"] for e in index["approved"]] == ["2025-06"]
    assert (
        release.last_approved_bundle(repo_settings)
        == repo_settings.releases_dir / "2025-06" / "estimates.json"
    )
    # approving again replaces the entry rather than duplicating it
    release.approve(repo_settings, "2025-06", bundle)
    assert len(release.load_index(repo_settings)["approved"]) == 1


def test_approve_refuses_a_different_run(repo_settings: Settings, tmp_path: Path) -> None:
    reviewed = _bundle(tmp_path / "est" / "estimates.json", run_id="r1")
    path = release.prepare(repo_settings, "2025-06", reviewed)
    _fill(path)
    newer = _bundle(tmp_path / "est2" / "estimates.json", run_id="r2")
    with pytest.raises(ValueError, match="review record is for run r1"):
        release.approve(repo_settings, "2025-06", newer)
    assert release.load_index(repo_settings)["approved"] == []


def test_reject_records_reason(repo_settings: Settings, tmp_path: Path) -> None:
    bundle = _bundle(tmp_path / "est" / "estimates.json")
    release.prepare(repo_settings, "2025-06", bundle)
    entry = release.reject(repo_settings, "2025-06", "placebo p too small", bundle, close_pr=False)
    assert entry["status"] == "rejected"
    assert entry["reason"] == "placebo p too small"
    assert release.load_index(repo_settings)["rejected"][0]["month"] == "2025-06"
    assert release.last_approved(repo_settings) is None


def test_build_report_and_site(repo_settings: Settings, tmp_path: Path) -> None:
    bundle = _bundle(tmp_path / "est" / "estimates.json")
    prev = _bundle(tmp_path / "prev" / "estimates.json", run_id="r0", data_end="2025-05-31")
    out = tmp_path / "REPORTS"
    report = build_report(repo_settings, bundle, prev, out_dir=out)
    text = report.read_text(encoding="utf-8")
    assert text.startswith("# Congestion pricing")
    assert "synthetic control" in text
    assert "| trips | prior year |" in text  # what-changed table
    assert "Last approved run `r0`" in text
    assert (out / "figures" / "comparisons.png").exists()
    assert (out / "estimates.json").exists()
    summary = bundle_summary(load_bundle(bundle))
    assert "### Synthetic control" in summary
    site = tmp_path / "site"
    written = build_site(out, site)
    assert (site / "index.html").exists()
    assert (site / "congestion_pricing.html").exists()
    assert any(p.name == "index.html" for p in written)
    page = (site / "congestion_pricing.html").read_text(encoding="utf-8")
    assert "<table>" in page
    assert "Not yet reviewed" in page


def test_site_banner_shows_approval(repo_settings: Settings, tmp_path: Path) -> None:
    bundle = _bundle(tmp_path / "est" / "estimates.json")
    path = release.prepare(repo_settings, "2025-06", bundle)
    _fill(path, decision="approve with notes")
    release.approve(repo_settings, "2025-06", bundle)
    out = tmp_path / "REPORTS"
    build_report(repo_settings, bundle, None, out_dir=out)
    site = tmp_path / "site"
    build_site(out, site, repo_settings.releases_dir)
    page = (site / "index.html").read_text(encoding="utf-8")
    assert "Reviewed release 2025-06" in page
    assert "Approved by VR on 2026-01-02" in page
    assert "Not yet reviewed" not in page
