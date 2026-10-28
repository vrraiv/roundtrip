from __future__ import annotations

import hashlib
from pathlib import Path

import httpx

from roundtrip.ingest import tlc

PAYLOAD = b"PAR1" + bytes(range(256)) * 4096 + b"PAR1"


def _transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("missing.parquet"):
            return httpx.Response(403)
        headers = {
            "content-length": str(len(PAYLOAD)),
            "etag": '"abc123"',
            "last-modified": "Tue, 01 Jul 2025 00:00:00 GMT",
        }
        if request.method == "HEAD":
            return httpx.Response(200, headers=headers)
        return httpx.Response(200, headers=headers, content=PAYLOAD)

    return httpx.MockTransport(handler)


def test_head_returns_metadata_or_none() -> None:
    with httpx.Client(transport=_transport()) as client:
        ok = tlc.head("https://cdn.test/trip-data/fhvhv_tripdata_2025-01.parquet", client)
        assert ok is not None
        assert ok.size_bytes == len(PAYLOAD) and ok.etag == '"abc123"'
        assert tlc.head("https://cdn.test/trip-data/missing.parquet", client) is None


def test_download_streams_and_hashes(tmp_path: Path) -> None:
    dest = tmp_path / "raw" / "f.parquet"
    with httpx.Client(transport=_transport()) as client:
        size, sha = tlc.download("https://cdn.test/trip-data/x.parquet", dest, client)
    assert size == len(PAYLOAD) == dest.stat().st_size
    assert sha == hashlib.sha256(PAYLOAD).hexdigest()
    assert not dest.with_suffix(".parquet.part").exists()


def test_file_url_roundtrip(tmp_path: Path) -> None:
    src = tmp_path / "cdn" / "trip-data" / "a.parquet"
    src.parent.mkdir(parents=True)
    src.write_bytes(PAYLOAD)
    url = src.as_uri()
    meta = tlc.head(url)
    assert meta is not None and meta.size_bytes == len(PAYLOAD)
    size, sha = tlc.download(url, tmp_path / "out.parquet")
    assert size == len(PAYLOAD) and sha == hashlib.sha256(PAYLOAD).hexdigest()
    assert tlc.head((tmp_path / "nope.parquet").as_uri()) is None
