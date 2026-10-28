"""Push/pull derived outputs to Cloudflare R2 through the S3-compatible API.

The bucket mirrors ``settings.derived_dir`` key-for-key (``manifest.json``,
``panel/zone_hour/month=YYYY-MM/data.parquet``, ...). Nothing under ``raw_cache`` is
ever uploaded.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING

import boto3

from roundtrip.config import Settings

if TYPE_CHECKING:
    from mypy_boto3_s3.client import S3Client

log = logging.getLogger(__name__)


class R2NotConfiguredError(RuntimeError):
    pass


def client(settings: Settings) -> S3Client:
    r2 = settings.r2
    if not r2.configured:
        msg = (
            "R2 is not configured: set R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, "
            "R2_SECRET_ACCESS_KEY and R2_BUCKET"
        )
        raise R2NotConfiguredError(msg)
    return boto3.client(
        "s3",
        endpoint_url=r2.endpoint_url,
        aws_access_key_id=r2.access_key_id,
        aws_secret_access_key=r2.secret_access_key,
        region_name="auto",
    )


def key_for(settings: Settings, path: Path) -> str:
    return path.resolve().relative_to(settings.derived_dir.resolve()).as_posix()


def push(settings: Settings, paths: Iterable[Path]) -> list[str]:
    """Upload the given files (which must live under ``derived_dir``). Returns keys."""
    s3 = client(settings)
    bucket = settings.r2.bucket
    assert bucket is not None
    keys: list[str] = []
    for p in paths:
        if not p.exists():
            log.warning("skip missing %s", p)
            continue
        key = key_for(settings, p)
        s3.upload_file(str(p), bucket, key)
        keys.append(key)
        log.info("pushed %s (%s bytes)", key, f"{p.stat().st_size:,}")
    return keys


def list_keys(settings: Settings, prefix: str = "") -> list[tuple[str, int]]:
    s3 = client(settings)
    bucket = settings.r2.bucket
    assert bucket is not None
    out: list[tuple[str, int]] = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            out.append((obj["Key"], int(obj.get("Size", 0))))
    return out


def pull(settings: Settings, prefix: str = "", *, overwrite: bool = False) -> list[Path]:
    """Download every object under ``prefix`` into ``derived_dir``. Skips same-size files."""
    s3 = client(settings)
    bucket = settings.r2.bucket
    assert bucket is not None
    pulled: list[Path] = []
    for key, size in list_keys(settings, prefix):
        dest = settings.derived_dir / key
        if dest.exists() and not overwrite and dest.stat().st_size == size:
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        s3.download_file(bucket, key, str(dest))
        pulled.append(dest)
        log.info("pulled %s (%s bytes)", key, f"{size:,}")
    return pulled


def pull_manifest(settings: Settings) -> Path | None:
    """Fetch ``manifest.json`` if it exists in the bucket."""
    s3 = client(settings)
    bucket = settings.r2.bucket
    assert bucket is not None
    keys = {k for k, _ in list_keys(settings, "manifest.json")}
    if "manifest.json" not in keys:
        return None
    dest = settings.manifest_path
    dest.parent.mkdir(parents=True, exist_ok=True)
    s3.download_file(bucket, "manifest.json", str(dest))
    return dest


def total_size(settings: Settings) -> int:
    return sum(size for _, size in list_keys(settings))
