"""Command-line interface: ``roundtrip <command>``."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Annotated

import typer

from roundtrip import __version__
from roundtrip.config import Settings, get_settings, validate_month

app = typer.Typer(
    help="NYC ride-hail marketplace economics pipeline.",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
)
release_app = typer.Typer(help="Human review gate (phase 2).", no_args_is_help=True)
app.add_typer(release_app, name="release")

MonthArg = Annotated[str, typer.Option("--month", "-m", help="Month as YYYY-MM.")]


def _settings() -> Settings:
    return get_settings()


@app.callback()
def _main(
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging.")] = False,
) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("botocore").setLevel(logging.WARNING)


@app.command()
def version() -> None:
    """Print the package version."""
    typer.echo(__version__)


@app.command()
def config() -> None:
    """Print the effective configuration (secrets redacted)."""
    s = _settings()
    typer.echo(json.dumps(s.model_dump(mode="json", exclude={"r2"}), indent=2, default=str))
    typer.echo(f"r2 configured: {s.r2.configured}")


@app.command()
def discover(
    end: Annotated[str | None, typer.Option(help="Last month to probe (default: now).")] = None,
    output_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """Probe TLC for months that are new, revised, or still unpublished."""
    from roundtrip.ingest import tlc
    from roundtrip.ingest.manifest import Manifest

    s = _settings()
    manifest = Manifest.load(s.manifest_path)
    d = tlc.discover(s, manifest, end_month=end)
    if output_json:
        typer.echo(json.dumps(d.__dict__))
        return
    typer.echo(f"new:         {d.new}")
    typer.echo(f"revised:     {d.revised}")
    typer.echo(f"unchanged:   {len(d.unchanged)} months")
    typer.echo(f"unavailable: {d.unavailable}")


@app.command()
def zones(force: Annotated[bool, typer.Option(help="Re-download.")] = False) -> None:
    """Download the TLC taxi zone lookup table."""
    from roundtrip.ingest.manifest import Manifest
    from roundtrip.pipeline import ensure_zone_lookup

    s = _settings()
    manifest = Manifest.load(s.manifest_path)
    path = ensure_zone_lookup(s, manifest, force=force)
    typer.echo(str(path))


@app.command()
def ingest(
    month: MonthArg,
    force: Annotated[bool, typer.Option(help="Re-download even if already processed.")] = False,
) -> None:
    """Stream one month's raw file into the local raw cache and print its checksum."""
    from roundtrip.ingest.manifest import Manifest
    from roundtrip.pipeline import SkipMonth, fetch_month

    s = _settings()
    validate_month(month)
    manifest = Manifest.load(s.manifest_path)
    try:
        path, size, sha, _ = fetch_month(s, manifest, month, force=force)
    except SkipMonth:
        typer.echo(f"{month}: already in manifest with matching bytes; use --force to refetch")
        raise typer.Exit(0) from None
    typer.echo(f"{path}  {size:,} bytes  sha256={sha}")


@app.command()
def validate(month: MonthArg) -> None:
    """Run the validation suite on a month already in the raw cache."""
    from roundtrip.db import connect, register_trips, register_zones
    from roundtrip.ingest.manifest import Manifest
    from roundtrip.pipeline import ensure_zone_lookup
    from roundtrip.validate import run_validation

    s = _settings()
    validate_month(month)
    raw = s.raw_path(month)
    if not raw.exists():
        typer.echo(f"{raw} not found; run `roundtrip ingest --month {month}` first", err=True)
        raise typer.Exit(2)
    manifest = Manifest.load(s.manifest_path)
    con = connect(s)
    register_zones(con, s, ensure_zone_lookup(s, manifest))
    columns = register_trips(con, s, raw)
    report = run_validation(con, s, manifest, month, columns)
    report.write(s.validation_path(month, "json"), s.validation_path(month, "md"))
    typer.echo(report.to_markdown())
    raise typer.Exit(1 if report.status == "fail" else 0)


@app.command("build-panel")
def build_panel_cmd(month: MonthArg) -> None:
    """Build the zone-hour panel and trip sample for a month in the raw cache."""
    from roundtrip.db import connect, register_trips, register_zones
    from roundtrip.features import build_panel, build_sample
    from roundtrip.ingest.manifest import Manifest
    from roundtrip.pipeline import ensure_zone_lookup

    s = _settings()
    validate_month(month)
    raw = s.raw_path(month)
    if not raw.exists():
        typer.echo(f"{raw} not found; run `roundtrip ingest --month {month}` first", err=True)
        raise typer.Exit(2)
    manifest = Manifest.load(s.manifest_path)
    con = connect(s)
    register_zones(con, s, ensure_zone_lookup(s, manifest))
    register_trips(con, s, raw)
    p, n_p = build_panel(con, s, month)
    q, n_q = build_sample(con, s, month)
    typer.echo(f"panel:  {n_p:,} rows -> {p}")
    typer.echo(f"sample: {n_q:,} rows -> {q}")


@app.command("process-month")
def process_month_cmd(
    month: MonthArg,
    force: Annotated[bool, typer.Option(help="Reprocess even if already in the manifest.")] = False,
    keep_raw: Annotated[bool, typer.Option(help="Keep the raw file in the cache.")] = False,
) -> None:
    """Fetch, validate, build panel and sample, record the run. One month per call."""
    from roundtrip.pipeline import process_month
    from roundtrip.validate import ValidationError

    s = _settings()
    validate_month(month)
    try:
        result = process_month(s, month, force=force, cleanup=not keep_raw)
    except ValidationError as e:
        typer.echo(e.report.to_markdown())
        typer.echo(f"VALIDATION FAILED for {month}", err=True)
        raise typer.Exit(1) from None
    typer.echo(f"{month}: {result.status}")
    if result.report is not None:
        typer.echo(f"validation: {result.report.status} ({result.report.row_count:,} rows)")
    for p in result.outputs:
        typer.echo(f"  {p}")
    typer.echo(f"wall: {result.wall_seconds:.0f}s")


@app.command()
def sync(
    prefix: Annotated[str, typer.Option(help="Only pull keys under this prefix.")] = "",
) -> None:
    """Pull derived outputs (manifest, panel, sample, validation, runs) from R2."""
    from roundtrip.storage import r2

    s = _settings()
    pulled = r2.pull(s, prefix)
    typer.echo(f"pulled {len(pulled)} files into {s.derived_dir}")


@app.command()
def push(
    month: Annotated[
        str | None, typer.Option("--month", "-m", help="Push one month's outputs.")
    ] = None,
    all_: Annotated[bool, typer.Option("--all", help="Push everything under derived/.")] = False,
) -> None:
    """Upload derived outputs to R2. Always includes manifest.json."""
    from roundtrip.storage import r2

    s = _settings()
    paths: list[Path] = [s.manifest_path]
    if all_:
        paths = [p for p in s.derived_dir.rglob("*") if p.is_file()]
    elif month:
        validate_month(month)
        paths += [
            s.panel_path(month),
            s.sample_path(month),
            s.validation_path(month, "json"),
            s.validation_path(month, "md"),
            s.zone_lookup_path,
        ]
        paths += [p for p in s.runs_dir.glob(f"*-{month}/run.json")]
    keys = r2.push(s, [p for p in paths if p.exists()])
    typer.echo(f"pushed {len(keys)} objects; bucket total {r2.total_size(s) / 1e6:.1f} MB")


# --- phase 2+ placeholders ------------------------------------------------------------------


def _not_yet(name: str, phase: int) -> None:
    typer.echo(f"`{name}` is implemented in phase {phase}.", err=True)
    raise typer.Exit(3)


@app.command("run-causal")
def run_causal_cmd(
    out: Annotated[
        Path | None,
        typer.Option("--out", help="Output directory (default data/derived/estimates/<run_id>)."),
    ] = None,
    quick: Annotated[
        bool, typer.Option("--quick", help="Fewer placebo draws and bootstrap replications.")
    ] = False,
) -> None:
    """Synthetic control, the three comparisons, event studies and placebos.

    Reads every panel month under data/derived (run ``sync --prefix panel/`` first) and
    writes an estimates bundle plus figures.
    """
    from roundtrip.causal.run import run_causal

    path = run_causal(_settings(), out_dir=out, quick=quick)
    typer.echo(str(path))


@app.command("estimates-summary")
def estimates_summary(
    path: Annotated[Path, typer.Argument(help="Path to an estimates.json bundle.")],
) -> None:
    """Print Markdown tables summarising an estimates bundle."""
    from roundtrip.report.summary import bundle_summary, load_bundle

    typer.echo(bundle_summary(load_bundle(path)))


@app.command()
def train() -> None:
    """Train wait-time and driver-pay models (phase 3)."""
    _not_yet("train", 3)


@app.command()
def score() -> None:
    """Score the new month and compute drift metrics (phase 3)."""
    _not_yet("score", 3)


@app.command()
def report(
    bundle: Annotated[
        Path | None,
        typer.Option("--bundle", help="estimates.json to report on (default: newest run)."),
    ] = None,
    out: Annotated[
        Path | None, typer.Option("--out", help="Report directory (default REPORTS/).")
    ] = None,
) -> None:
    """Build REPORTS/congestion_pricing.md and figures from an estimates bundle, with the
    review dashboard diffed against the last approved release."""
    from roundtrip.release import last_approved_bundle
    from roundtrip.report.build import build_report

    s = _settings()
    if bundle is None:
        runs = sorted(s.estimates_dir.glob("*/estimates.json"))
        if not runs:
            typer.echo("no estimates bundle found; run `roundtrip run-causal` first", err=True)
            raise typer.Exit(code=1)
        bundle = runs[-1]
    path = build_report(s, bundle, last_approved_bundle(s), out_dir=out)
    typer.echo(str(path))


@app.command("build-site")
def build_site_cmd(
    out: Annotated[Path, typer.Option("--out", help="Output directory.")] = Path("site"),
) -> None:
    """Build the public site (home, one page per outcome, method, report, releases with the
    release archive, and the review records) from REPORTS/, releases/ and reviews/ for
    Cloudflare Pages."""
    from roundtrip.report.site import build_site

    s = _settings()
    for p in build_site(s.reports_dir, out, s.releases_dir, s.pages_dir, s.repo_url, s.reviews_dir):
        typer.echo(str(p))


BundleOpt = Annotated[
    Path | None, typer.Option("--bundle", help="estimates.json of the candidate run.")
]


@release_app.command("prepare")
def release_prepare(
    month: MonthArg,
    bundle: BundleOpt = None,
    force: Annotated[bool, typer.Option("--force", help="Overwrite an existing record.")] = False,
) -> None:
    """Create reviews/YYYY-MM.md from the template for the reviewer to fill in."""
    from roundtrip import release

    typer.echo(str(release.prepare(_settings(), month, bundle, force=force)))


@release_app.command("check")
def release_check(month: MonthArg) -> None:
    """Fail unless reviews/YYYY-MM.md exists and every field is filled in (CI check)."""
    from roundtrip import release

    problems = release.check(release.review_path(_settings(), month))
    if problems:
        for p in problems:
            typer.echo(f"review record: {p}", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"review record for {month} is complete")


@release_app.command("approve")
def release_approve(month: MonthArg, bundle: BundleOpt = None) -> None:
    """Record an approval in releases/index.json and keep the approved bundle."""
    from roundtrip import release

    s = _settings()
    b = bundle or s.reports_dir / "estimates.json"
    try:
        entry = release.approve(s, month, b)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(entry, indent=2))


@release_app.command("from-issue")
def release_from_issue(
    body_file: Annotated[
        Path, typer.Option("--body-file", help="Body of the submitted review issue form.")
    ],
    reviewer: Annotated[str, typer.Option("--reviewer", help="GitHub login of the reviewer.")],
    date: Annotated[str, typer.Option("--date", help="Review date, YYYY-MM-DD.")],
    bundle: BundleOpt = None,
) -> None:
    """Write reviews/YYYY-MM.md from a review issue form, check it, and print what it says.

    Prints JSON (month, run_id, decision, reason, path, problems). Exits 1 if the form names
    a different run than the candidate bundle or the record is incomplete.
    """
    from roundtrip import release

    s = _settings()
    fields = release.parse_form(body_file.read_text(encoding="utf-8"))
    try:
        path, problems = release.from_form(
            s, fields, reviewer, date, bundle or s.reports_dir / "estimates.json"
        )
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    out = {
        "month": fields.get("Month", "").strip(),
        "run_id": fields.get("Run id", "").strip(),
        "decision": release.decision_of(release.parse(path)),
        "reason": fields.get("Notes", "").strip() or fields.get("Known issues", "").strip(),
        "path": str(path),
        "problems": problems,
    }
    typer.echo(json.dumps(out, indent=2))
    if problems:
        typer.echo("review record incomplete: " + "; ".join(problems), err=True)
        raise typer.Exit(code=1)


@release_app.command("reject")
def release_reject(
    month: MonthArg,
    reason: Annotated[str, typer.Option("--reason", help="Why the candidate was rejected.")],
    bundle: BundleOpt = None,
    no_close: Annotated[bool, typer.Option("--no-close", help="Do not close the PR.")] = False,
) -> None:
    """Record a rejection with its reason and close the candidate PR."""
    from roundtrip import release

    entry = release.reject(_settings(), month, reason, bundle, close_pr=not no_close)
    typer.echo(json.dumps(entry, indent=2))


if __name__ == "__main__":  # pragma: no cover
    app()
