"""Per-month pipeline: fetch -> validate -> build panel + sample -> record run.

This is what the monthly and backfill workflows call, one month per job step. The raw
file is streamed to a transient cache, hashed, read by DuckDB, and deleted. Only the
derived outputs under ``derived/`` are kept (and mirrored to R2).
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import platform
import subprocess
import time
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import duckdb

from roundtrip import __version__
from roundtrip.config import Settings
from roundtrip.db import connect, register_trips, register_zones
from roundtrip.features import build_panel, build_sample
from roundtrip.ingest import tlc
from roundtrip.ingest.manifest import Manifest, MonthRecord, ZoneLookupRecord, sha256_file
from roundtrip.validate import ValidationError, ValidationReport, run_validation

log = logging.getLogger(__name__)


def git_sha() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None


def _pkg_version(name: str) -> str | None:
    try:
        return version(name)
    except PackageNotFoundError:
        return None


class SkipMonth(Exception):  # noqa: N818 - control-flow signal, not an error
    """Raised when a month is already processed with matching bytes (idempotent skip)."""


@dataclass
class MonthResult:
    month: str
    status: str  # "processed" | "skipped" | "failed"
    report: ValidationReport | None = None
    outputs: list[Path] = field(default_factory=list)
    run_id: str | None = None
    wall_seconds: float = 0.0


def ensure_zone_lookup(settings: Settings, manifest: Manifest, *, force: bool = False) -> Path:
    """Download the TLC taxi zone lookup once and record it in the manifest."""
    path = settings.zone_lookup_path
    if path.exists() and manifest.zone_lookup is not None and not force:
        return path
    url = settings.zone_lookup_url
    size, sha = tlc.download(url, path)
    with path.open("r", encoding="utf-8") as f:
        n_zones = max(sum(1 for _ in f) - 1, 0)
    manifest.zone_lookup = ZoneLookupRecord(
        url=url,
        size_bytes=size,
        sha256=sha,
        fetched_at=dt.datetime.now(dt.UTC),
        n_zones=n_zones,
    )
    manifest.save(settings.manifest_path)
    log.info("zone lookup: %d zones from %s", n_zones, url)
    return path


def fetch_month(
    settings: Settings,
    manifest: Manifest,
    month: str,
    *,
    force: bool = False,
) -> tuple[Path, int, str, tlc.RemoteFile]:
    """Download ``month`` to the raw cache unless already processed with the same bytes.

    Returns ``(path, size, sha256, remote)``. Raises :class:`SkipMonth` when the manifest
    already has this month and the remote size/etag match (or when a revision is detected
    without ``force``, after logging it loudly).
    """
    url = settings.raw_url(month)
    remote = tlc.head(url)
    if remote is None:
        msg = f"{url} is not available (TLC has not published {month} yet?)"
        raise FileNotFoundError(msg)

    record = manifest.months.get(month)
    if record is not None and not force:
        if tlc.is_revision(record, remote):
            log.warning(
                "%s: TLC revision detected (size %s -> %s). Rerun with --force to reprocess.",
                month,
                f"{record.size_bytes:,}",
                f"{remote.size_bytes:,}" if remote.size_bytes else "?",
            )
        raise SkipMonth(month)

    dest = settings.raw_path(month)
    if dest.exists() and remote.size_bytes == dest.stat().st_size:
        log.info("%s: reusing cached raw file %s", month, dest)
        return dest, dest.stat().st_size, sha256_file(dest), remote

    t0 = time.perf_counter()
    size, sha = tlc.download(url, dest)
    log.info("%s: downloaded %s bytes in %.0fs", month, f"{size:,}", time.perf_counter() - t0)
    return dest, size, sha, remote


def write_run_json(
    settings: Settings,
    manifest: Manifest,
    *,
    month: str,
    status: str,
    report: ValidationReport | None,
    outputs: list[Path],
    wall_seconds: float,
    peak_rss_mb: float | None,
) -> tuple[str, Path]:
    run_id = f"{dt.datetime.now(dt.UTC):%Y%m%dT%H%M%SZ}-{month}"
    path = settings.runs_dir / run_id / "run.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "run_id": run_id,
        "month": month,
        "status": status,
        "git_sha": git_sha(),
        "manifest_hash": manifest.content_hash(),
        "validation_status": report.status if report else None,
        "outputs": [
            p.resolve().relative_to(settings.derived_dir.resolve()).as_posix() for p in outputs
        ],
        "wall_seconds": round(wall_seconds, 1),
        "peak_rss_mb": peak_rss_mb,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "packages": {
            "roundtrip": __version__,
            "duckdb": duckdb.__version__,
            "pydantic": _pkg_version("pydantic"),
            "pyarrow": _pkg_version("pyarrow"),
        },
        "config": settings.model_dump(mode="json", exclude={"r2"}),
    }
    path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    return run_id, path


def _peak_rss_mb() -> float | None:
    """Peak resident set size of this process in MB (None on Windows)."""
    try:
        import resource
    except ImportError:
        return None
    usage = resource.getrusage(resource.RUSAGE_SELF)  # type: ignore[attr-defined, unused-ignore]
    kb = float(usage.ru_maxrss)
    # Linux reports kilobytes, macOS bytes.
    divisor = 1024 * 1024 if platform.system() == "Darwin" else 1024
    return round(kb / divisor, 1)


def process_month(
    settings: Settings,
    month: str,
    *,
    force: bool = False,
    cleanup: bool = True,
    build: bool = True,
) -> MonthResult:
    """Run the full per-month pipeline. Idempotent: already-processed months are skipped.

    Raises :class:`ValidationError` on a hard validation failure after writing the report
    and marking the month ``fail`` in the manifest (so the next run does not silently
    retry it as new; a fix needs ``--force``).
    """
    t0 = time.perf_counter()
    manifest = Manifest.load(settings.manifest_path)
    try:
        raw_path, size, sha, remote = fetch_month(settings, manifest, month, force=force)
    except SkipMonth:
        log.info("%s: already processed, skipping", month)
        return MonthResult(month, "skipped")

    previous = manifest.months.get(month)
    zones_csv = ensure_zone_lookup(settings, manifest)

    con = connect(settings)
    try:
        register_zones(con, settings, zones_csv)
        columns = register_trips(con, settings, raw_path)
        report = run_validation(con, settings, manifest, month, columns)
        report.write(settings.validation_path(month, "json"), settings.validation_path(month, "md"))

        record = MonthRecord(
            month=month,
            url=remote.url,
            size_bytes=size,
            sha256=sha,
            etag=remote.etag,
            last_modified=remote.last_modified,
            row_count=report.row_count,
            columns=columns,
            null_rates=report.null_rates,
            processed_at=dt.datetime.now(dt.UTC),
            package_version=__version__,
            duckdb_version=duckdb.__version__,
            git_sha=git_sha(),
            validation_status=report.status,
            revision_of=previous.sha256 if previous and previous.sha256 != sha else None,
        )
        manifest.months[month] = record
        manifest.save(settings.manifest_path)

        outputs = [
            settings.validation_path(month, "json"),
            settings.validation_path(month, "md"),
            settings.manifest_path,
        ]
        if report.status == "fail":
            run_id, run_path = write_run_json(
                settings,
                manifest,
                month=month,
                status="failed",
                report=report,
                outputs=outputs,
                wall_seconds=time.perf_counter() - t0,
                peak_rss_mb=_peak_rss_mb(),
            )
            raise ValidationError(report)

        if build:
            panel_path, _ = build_panel(con, settings, month)
            sample_path, _ = build_sample(con, settings, month)
            outputs = [panel_path, sample_path, *outputs]
    finally:
        con.close()
        if cleanup and raw_path.exists():
            raw_path.unlink()
            log.info("%s: deleted raw file", month)

    wall = time.perf_counter() - t0
    run_id, run_path = write_run_json(
        settings,
        manifest,
        month=month,
        status="processed",
        report=report,
        outputs=outputs,
        wall_seconds=wall,
        peak_rss_mb=_peak_rss_mb(),
    )
    outputs.append(run_path)
    log.info("%s: done in %.0fs (run %s)", month, wall, run_id)
    return MonthResult(month, "processed", report, outputs, run_id, wall)
