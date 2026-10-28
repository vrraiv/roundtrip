"""Talk to TLC's CloudFront bucket: probe, stream, checksum.

Only ``https://`` and ``file://`` sources are supported. The ``file://`` form points at a
directory laid out like the CDN (``trip-data/<file>``, ``misc/<file>``) and exists so that
tests and offline runs use exactly the same code path as production.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

import httpx

from roundtrip.config import Settings, current_month, month_range
from roundtrip.ingest.manifest import Manifest, MonthRecord

log = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(connect=30.0, read=120.0, write=30.0, pool=30.0)


@dataclass(frozen=True)
class RemoteFile:
    url: str
    size_bytes: int | None
    etag: str | None
    last_modified: str | None


def _file_url_to_path(url: str) -> Path:
    parsed = urlparse(url)
    # file:///C:/x/y  ->  C:/x/y ; file:///home/x -> /home/x
    raw = unquote(parsed.path)
    if raw.startswith("/") and len(raw) > 2 and raw[2] == ":":
        raw = raw[1:]
    return Path(raw)


def head(url: str, client: httpx.Client | None = None) -> RemoteFile | None:
    """Return remote metadata, or ``None`` if the file does not exist (HTTP 403/404)."""
    if url.startswith("file://"):
        path = _file_url_to_path(url)
        if not path.exists():
            return None
        stat = path.stat()
        return RemoteFile(url, stat.st_size, None, str(int(stat.st_mtime)))

    own = client is None
    client = client or httpx.Client(timeout=_TIMEOUT, follow_redirects=True)
    try:
        resp = client.head(url)
    finally:
        if own:
            client.close()
    # CloudFront answers 403 for keys that are not there.
    if resp.status_code in (403, 404):
        return None
    resp.raise_for_status()
    size = resp.headers.get("content-length")
    return RemoteFile(
        url=url,
        size_bytes=int(size) if size else None,
        etag=resp.headers.get("etag"),
        last_modified=resp.headers.get("last-modified"),
    )


def download(url: str, dest: Path, client: httpx.Client | None = None) -> tuple[int, str]:
    """Stream ``url`` to ``dest`` and return ``(size_bytes, sha256)``.

    The hash is computed on the bytes as they stream so the file is read exactly once.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha256()
    size = 0
    tmp = dest.with_suffix(dest.suffix + ".part")

    if url.startswith("file://"):
        src = _file_url_to_path(url)
        with src.open("rb") as f, tmp.open("wb") as out:
            while chunk := f.read(1 << 20):
                h.update(chunk)
                out.write(chunk)
                size += len(chunk)
        shutil.move(str(tmp), str(dest))
        return size, h.hexdigest()

    own = client is None
    client = client or httpx.Client(timeout=_TIMEOUT, follow_redirects=True)
    try:
        with client.stream("GET", url) as resp, tmp.open("wb") as out:
            resp.raise_for_status()
            for chunk in resp.iter_bytes(chunk_size=1 << 20):
                h.update(chunk)
                out.write(chunk)
                size += len(chunk)
    finally:
        if own:
            client.close()
    shutil.move(str(tmp), str(dest))
    return size, h.hexdigest()


def is_revision(record: MonthRecord, remote: RemoteFile) -> bool:
    """True if TLC appears to have republished a month we already processed.

    Compares size and ETag; a full SHA-256 comparison would require re-downloading.
    """
    if remote.size_bytes is not None and remote.size_bytes != record.size_bytes:
        return True
    return bool(remote.etag and record.etag and remote.etag != record.etag)


@dataclass(frozen=True)
class Discovery:
    new: list[str]
    revised: list[str]
    unavailable: list[str]
    unchanged: list[str]


def discover(
    settings: Settings,
    manifest: Manifest,
    *,
    end_month: str | None = None,
    client: httpx.Client | None = None,
) -> Discovery:
    """Probe every month from ``start_month`` to ``end_month`` and classify it.

    TLC publishes with roughly a two-month lag, so probing up to the current month is cheap
    (one HEAD per month) and tolerant of out-of-order publication.
    """
    end = end_month or settings.end_month or current_month()
    new: list[str] = []
    revised: list[str] = []
    unavailable: list[str] = []
    unchanged: list[str] = []
    own = client is None
    client = client or httpx.Client(timeout=_TIMEOUT, follow_redirects=True)
    try:
        for month in month_range(settings.start_month, end):
            remote = head(settings.raw_url(month), client)
            if remote is None:
                unavailable.append(month)
                continue
            record = manifest.months.get(month)
            if record is None:
                new.append(month)
            elif is_revision(record, remote):
                log.warning("TLC revision detected for %s (size/etag changed)", month)
                revised.append(month)
            else:
                unchanged.append(month)
    finally:
        if own:
            client.close()
    return Discovery(new=new, revised=revised, unavailable=unavailable, unchanged=unchanged)
