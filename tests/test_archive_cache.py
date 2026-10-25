import hashlib
import io
from pathlib import Path

import pytest

from app.cli import cache_archive


class Response(io.BytesIO):
    status = 206

    def __init__(self, payload, content_range):
        super().__init__(payload)
        self.headers = {"Content-Range": content_range}


def test_range_assembly_hash_and_offline_cache_reuse(tmp_path, monkeypatch):
    payload = b"abcdefgh"
    digest = hashlib.sha256(payload).hexdigest()
    monkeypatch.setattr(cache_archive, "ARCHIVE_BYTES", 8)
    monkeypatch.setattr(cache_archive, "PART_BYTES", 4)
    monkeypatch.setattr(cache_archive, "ARCHIVE_SHA256_DECLARED", digest)
    monkeypatch.setattr(cache_archive, "__file__", str(tmp_path / "app" / "cli" / "cache_archive.py"))
    calls = []

    def fake_open(request, timeout):
        start, end = map(int, request.get_header("Range").removeprefix("bytes=").split("-"))
        calls.append((start, end))
        return Response(payload[start:end + 1], f"bytes {start}-{end}/8")

    monkeypatch.setattr(cache_archive, "urlopen", fake_open)
    result = cache_archive.ensure_archive()
    assert result.read_bytes() == payload
    assert sorted(calls) == [(0, 3), (4, 7)]
    assert cache_archive.ensure_archive() == result
    assert len(calls) == 2  # Existing full cache is hashed and reused without network.
    assert not list(result.parent.glob("*.partial"))
    assert not list(result.parent.glob("*.part"))


def test_ignored_range_cannot_be_accepted_as_chunk(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_archive, "ARCHIVE_BYTES", 4)
    monkeypatch.setattr(cache_archive, "PART_BYTES", 4)
    monkeypatch.setattr(cache_archive.time, "sleep", lambda _: None)
    monkeypatch.setattr(cache_archive, "urlopen", lambda *a, **k: Response(b"abcd", "bytes 0-1/4"))
    with pytest.raises(RuntimeError, match="honor requested range"):
        cache_archive.fetch_part(tmp_path, 0)
    assert not list(tmp_path.iterdir())


def test_wrong_full_hash_never_publishes_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_archive, "ARCHIVE_BYTES", 4)
    monkeypatch.setattr(cache_archive, "PART_BYTES", 4)
    monkeypatch.setattr(cache_archive, "ARCHIVE_SHA256_DECLARED", "0" * 64)
    monkeypatch.setattr(cache_archive, "__file__", str(tmp_path / "app" / "cli" / "cache_archive.py"))
    monkeypatch.setattr(cache_archive, "urlopen", lambda *a, **k: Response(b"abcd", "bytes 0-3/4"))
    with pytest.raises(ValueError, match="size/hash"):
        cache_archive.ensure_archive()
    assert not list((tmp_path / "data" / "raw" / "tabformer").glob("*.tgz"))
