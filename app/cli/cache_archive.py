"""Cache and verify the fixed official synthetic archive for local research."""

from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import time
from urllib.request import Request, urlopen
from uuid import uuid4

from scripts.probe_tabformer import ARCHIVE_SHA256_DECLARED, URL


ARCHIVE_BYTES = 278576638
PART_BYTES = 4 * 1024 * 1024


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch_part(cache: Path, number: int) -> Path:
    """Resume fixed byte ranges; accept only the exact requested Content-Range."""
    start = number * PART_BYTES
    end = min(start + PART_BYTES, ARCHIVE_BYTES) - 1
    target = cache / f"{ARCHIVE_SHA256_DECLARED}.{number:03}.part"
    expected = end - start + 1
    if target.exists():
        if target.stat().st_size != expected:
            raise ValueError("Existing range cache has wrong size")
        return target
    last_error = None
    for attempt in range(2):
        temporary = cache / f"{uuid4().hex}.partial"
        try:
            request = Request(URL, headers={
                "User-Agent": "RiskCase-local-research/0.1", "Range": f"bytes={start}-{end}",
            })
            began = time.monotonic()
            with urlopen(request, timeout=20) as response, temporary.open("xb") as output:
                if response.status != 206 or response.headers.get("Content-Range") != f"bytes {start}-{end}/{ARCHIVE_BYTES}":
                    raise ValueError("Server did not honor requested range")
                total = 0
                while chunk := response.read(256 * 1024):
                    total += len(chunk)
                    if total > expected or time.monotonic() - began > 90:
                        raise ValueError("Range size/time budget exceeded")
                    output.write(chunk)
            if total != expected:
                raise ValueError("Truncated range response")
            temporary.rename(target)
            return target
        except Exception as exc:
            last_error = exc
            if attempt == 0:
                time.sleep(0.5)
        finally:
            if temporary.exists():
                temporary.unlink()
    raise RuntimeError(f"Range {number} failed: {last_error}") from last_error


def ensure_archive() -> Path:
    cache = Path(__file__).resolve().parents[2] / "data" / "raw" / "tabformer"
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / f"{ARCHIVE_SHA256_DECLARED}.tgz"
    if target.exists():
        if target.stat().st_size != ARCHIVE_BYTES or file_sha256(target) != ARCHIVE_SHA256_DECLARED:
            raise ValueError("Existing cache has unexpected size/hash; no overwrite performed")
        return target
    part_count = (ARCHIVE_BYTES + PART_BYTES - 1) // PART_BYTES
    parts = {}
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(fetch_part, cache, number): number for number in range(part_count)}
        for future in as_completed(futures):
            number = futures[future]
            parts[number] = future.result()
            if len(parts) % 8 == 0 or len(parts) == part_count:
                print(f"Verified range sizes: {len(parts)}/{part_count}; full hash still pending", flush=True)
    temporary = cache / f"{uuid4().hex}.partial"
    digest = hashlib.sha256()
    total = 0
    try:
        with temporary.open("xb") as output:
            for number in range(part_count):
                with parts[number].open("rb") as part:
                    for chunk in iter(lambda: part.read(1024 * 1024), b""):
                        total += len(chunk)
                        output.write(chunk)
                        digest.update(chunk)
        if total != ARCHIVE_BYTES or digest.hexdigest() != ARCHIVE_SHA256_DECLARED:
            # No trusted per-part hashes exist. A failed full hash invalidates
            # this assembled set; do not get stuck reusing corrupt range files.
            for part in parts.values():
                part.unlink()
            raise ValueError("Downloaded archive does not match declared size/hash")
        if target.exists():
            raise FileExistsError("Cache was created concurrently; refusing replacement")
        temporary.rename(target)
        for part in parts.values():
            part.unlink()
        return target
    finally:
        if temporary.exists():
            temporary.unlink()


if __name__ == "__main__":
    print(f"Verified local archive: {ensure_archive()}")
