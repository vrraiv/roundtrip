"""Review gate, report builder and site builder."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from roundtrip import release
from roundtrip.config import Settings
from roundtrip.report.build import build_report
from roundtrip.report.site import build_site, review_records
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
                "series": {
                    "day": [f"2024-12-{d:02d}" for d in range(1, 29)],
                    "actual": [100.0 + d for d in range(28)],
                    "synthetic": [110.0 + d for d in range(28)],
                },
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
    for name in ("index", "trips", "waits", "fares", "method", "report", "releases"):
        assert (site / f"{name}.html").exists(), name
    assert any(p.name == "index.html" for p in written)
    page = (site / "report.html").read_text(encoding="utf-8")
    assert "<table>" in page
    assert "Not yet reviewed" in page
    home = (site / "index.html").read_text(encoding="utf-8")
    # the finding block carries the range across the three designs (-9, -8, -7)
    assert "-7.0 to -9.0%" in home
    assert "CBD pickups were 7.0 to 9.0% lower than in comparison zones" in home
    # the effect chart is inline SVG with a table of the same numbers under it
    assert 'class="panel viz viz-dots"' in home and "<svg" in home
    assert "<td>Trips</td><td>synthetic control</td><td>-8.0%</td>" in home
    trips = (site / "trips.html").read_text(encoding="utf-8")
    assert 'class="panel viz viz-lines"' in trips
    assert "<td>2024-12-01</td><td>103</td><td>113</td>" in trips
    assert "figures/synth_n_trips.png" not in trips
    assert (site / "figures" / "comparisons.png").exists()  # still used by the report page
    assert "No reviewed release yet" in (site / "releases.html").read_text(encoding="utf-8")
    assert "method page has not been written" in (site / "method.html").read_text(encoding="utf-8")


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
    # the approved release is archived from its own bundle, without figures
    archived = site / "releases" / "2025-06" / "index.html"
    assert archived.exists()
    assert "-7.0 to -9.0%" in archived.read_text(encoding="utf-8")
    assert "viz-dots" in archived.read_text(encoding="utf-8")  # charts come from the bundle
    assert "viz-lines" in (site / "releases" / "2025-06" / "trips.html").read_text(encoding="utf-8")
    assert not (site / "releases" / "2025-06" / "figures").exists()
    releases = (site / "releases.html").read_text(encoding="utf-8")
    assert '<a href="releases/2025-06/index.html">2025-06</a>' in releases
    assert "approve with notes" in releases
    assert "release 2025-06, estimates bundle at git <code>abcdef123456</code>" in releases


ADHOC = """# Review record: donor pool

One sentence that says what was decided.

## Context

Why the question came up.

## Decisions

| # | Option | Recommendation | Decision |
|---|---|---|---|
| 1 | Drop Lenox Hill from the donors | Try as one variant | Later |

## Reviewer

Initials: VR
Date: 2026-01-15
"""


def test_site_renders_review_records(repo_settings: Settings, tmp_path: Path) -> None:
    bundle = _bundle(tmp_path / "est" / "estimates.json")
    path = release.prepare(repo_settings, "2025-06", bundle)
    _fill(path, decision="approve with notes")
    release.approve(repo_settings, "2025-06", bundle)
    adhoc = repo_settings.reviews_dir / "2026-01-15-donor-pool.md"
    adhoc.write_text(ADHOC, encoding="utf-8")
    records = review_records(repo_settings.reviews_dir)
    assert [r.stem for r in records] == ["2026-01-15-donor-pool", "2025-06"]  # newest first
    assert records[0].title == "Review record: donor pool"
    assert records[0].summary == "One sentence that says what was decided."
    assert (records[0].reviewer, records[0].date, records[0].monthly) == ("VR", "2026-01-15", False)
    assert records[1].monthly and records[1].date == "2026-01-02"
    out = tmp_path / "REPORTS"
    build_report(repo_settings, bundle, None, out_dir=out)
    site = tmp_path / "site"
    build_site(out, site, repo_settings.releases_dir, reviews_dir=repo_settings.reviews_dir)
    page = (site / "reviews" / "2026-01-15-donor-pool.html").read_text(encoding="utf-8")
    assert "<h1>Review record: donor pool</h1>" in page
    assert "<td>Drop Lenox Hill from the donors</td>" in page
    assert "Reviewed release" not in page  # no release status line on a record
    assert 'href="../index.html"' in page and 'href="../releases.html" aria-current' in page
    assert (site / "reviews" / "2025-06.html").exists()
    releases = (site / "releases.html").read_text(encoding="utf-8")
    assert '<a href="reviews/2025-06.html">Review record</a>' in releases
    assert "<h2>Other reviews</h2>" in releases
    assert '<a href="reviews/2026-01-15-donor-pool.html">Review record: donor pool</a>' in releases
    assert "<td>One sentence that says what was decided.</td>" in releases
    # without a reviews directory the page is as before
    build_site(out, tmp_path / "site2", repo_settings.releases_dir)
    assert "Other reviews" not in (tmp_path / "site2" / "releases.html").read_text(encoding="utf-8")


def test_committed_review_records_parse() -> None:
    records = {r.stem: r for r in review_records(REPO / "reviews")}
    redesign = records["2026-10-29-synth-redesign"]
    assert not redesign.monthly
    assert (redesign.reviewer, redesign.date) == ("vrraiv", "2026-10-29")
    assert redesign.summary.startswith("Fourteen decisions")
    assert (
        redesign.text.count("| Yes |")
        + redesign.text.count("| No |")
        + redesign.text.count("| Later |")
        == 14
    )
    assert records["2026-08"].monthly and records["2026-08"].reviewer == "vrraiv"


def test_trust_reads_the_rule_and_the_break() -> None:
    from roundtrip.report.site import OUTCOME_BY_KEY, finding_sentence, outcome_html, trust

    def horizon(bias: float, n: int, within: bool) -> dict[str, object]:
        return {
            "n_origins": n,
            "bias_pct": bias,
            "within": within,
            "rank_p": 0.7,
            "max_abs_gap_pct": abs(bias),
            "rmspe_pct": 4.0,
        }

    rule = {
        "horizons": {"1-3": horizon(0.2, 3, True), "7-12": horizon(-0.3, 1, True)},
        "rmspe_pct": 4.0,
        "passes": True,
    }
    every = {
        "horizons": {"1-3": horizon(-0.3, 6, True), "7-12": horizon(-3.1, 4, False)},
        "rmspe_pct": 4.5,
        "passes": False,
    }
    origins = ["2023-06-30", "2023-09-30", "2023-12-31", "2024-03-31", "2024-06-30", "2024-09-30"]
    bundle = {
        "estimates": [],
        "windows": {},
        "synth": {
            "n_trips": {
                "design": "seasonal_step",
                "params": {
                    "step": {"applied": True, "break_week": "2023-12-18", "shift_pct": -2.9}
                },
                "windows": {"post": {"mean_gap_pct": -4.2}},
                "series": {"day": [], "actual": [], "synthetic": []},
                "guard": {"design": "seasonal_step", "rolling_passes": True},
            }
        },
        "synth_oos": {
            "n_trips": {
                "windows": {"holdout": ["2023-11-14", "2024-11-13"]},
                "models": {"seasonal_step": {"windows": {"holdout": {"mean_gap_pct": -4.0}}}},
                "verdict": {"seasonal_step": {"passes": False, "counts": True}},
            }
        },
        "synth_rolling": {
            "n_trips": {
                "origins": origins,
                "rule_origins": origins[3:],
                "horizons": {"1-3": [1, 3], "7-12": [7, 12]},
                "tolerance_pct": 2.0,
                "summary": {"seasonal_step": rule},
                "summary_all": {"seasonal_step": every},
                "selected": "seasonal_step",
            }
        },
    }
    d = trust(bundle, "n_trips")
    assert d is not None and d["passes"] and d["design"] == "seasonal_step"
    assert d["break_week"] == "2023-12-18" and d["holdout_passes"] is False
    s = finding_sentence(OUTCOME_BY_KEY["n_trips"], (-4.8, -4.2), d)
    assert "puts it at 4.2%" in s and "December 2023" in s and "drifted" not in s
    page = outcome_html(OUTCOME_BY_KEY["n_trips"], bundle)
    assert "from 2024-03-31 on" in page and "The comparison passes." in page
    assert "only the synthetic control" in page and "<td>-3.1%</td>" in page
    assert "outside tolerance" in page
    assert trust({"synth": {"n_trips": {}}}, "n_trips") is None
    # an older bundle with the hold-out only still reads the simplex verdict
    old = {
        "synth_oos": {
            "n_trips": {
                "windows": {"holdout": ["a", "b"]},
                "models": {"simplex": {"windows": {"holdout": {"mean_gap_pct": -4.5}}}},
                "verdict": {
                    "simplex": {"passes": False, "counts": True},
                    "ridge": {"passes": False, "counts": True},
                },
            }
        }
    }
    o = trust(old, "n_trips")
    assert o is not None and not o["passes"] and o["holdout_gap_pct"] == -4.5
    assert "drifted by about -4.5%" in finding_sentence(OUTCOME_BY_KEY["n_trips"], (-5.6, -4.6), o)
