"""Profile the entire verified local CSV without downloading or extracting it."""

from __future__ import annotations

import codecs
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
import time
from uuid import uuid4

from app.cli.cache_archive import ARCHIVE_BYTES, file_sha256
from app.risk.profiling import profile_rows
from scripts.probe_tabformer import ARCHIVE_SHA256_DECLARED, SOURCE_COMMIT, URL


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="New output directory; existing directories are never overwritten")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    source = root / "data/raw/tabformer" / f"{ARCHIVE_SHA256_DECLARED}.tgz"
    if not source.is_file():
        raise FileNotFoundError("Verified local archive required; this command never downloads")
    if source.stat().st_size != ARCHIVE_BYTES or file_sha256(source) != ARCHIVE_SHA256_DECLARED:
        raise ValueError("Archive size/hash mismatch")
    output = args.output.resolve() if args.output is not None else root / "data/profiles" / f"profile-{uuid4().hex[:12]}"
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    with tarfile.open(source, mode="r|gz") as archive:
        for member in archive:
            if member.name != "card_transaction.v1.csv" or not member.isfile():
                continue
            handle = archive.extractfile(member)
            if handle is None:
                raise ValueError("CSV unreadable")
            with codecs.getreader("utf-8-sig")(handle) as text:
                reader = csv.DictReader(text)
                columns = reader.fieldnames or []
                stats = profile_rows(reader, columns, lambda n: print(f"Profiled {n:,} rows", flush=True))
            break
        else:
            raise ValueError("Expected CSV missing")
    # Publish only after full CSV EOF and aggregate reconciliation.
    code_files = [Path(__file__), root / "app/risk/profiling.py"]
    manifest = {
        "status": "complete_source_inventory", "created_at": datetime.now(timezone.utc).isoformat(),
        "source_url": URL, "source_commit": SOURCE_COMMIT,
        "archive_sha256_verified": ARCHIVE_SHA256_DECLARED, "source_csv_fully_scanned": True,
        "data_kind": "synthetic", "profile_version": 1,
        "code_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
        "code_file_sha256": {str(p.relative_to(root)): file_sha256(p) for p in code_files},
        "elapsed_seconds": round(time.monotonic() - started, 3), **stats,
        "limitations": [
            "Dataset eligibility inventory only, not model evaluation or a population fraud estimate.",
            "Labels are synthetic outcomes, not analyst-confirmed real fraud.",
            "No known label/ingestion availability times, currency or timezone contract.",
            "No duplicate, geography, merchant-key consistency or feature leakage audit yet.",
            "Do not select customers by positive labels or tune models using future holdout statistics.",
        ],
    }
    content = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    target = output / "profile.json"
    target.write_bytes(content)
    summary = {k: manifest[k] for k in (
        "status", "total_rows", "valid_rows", "rejected_rows", "overall", "fraud_fraction_of_valid_rows",
        "distinct_users", "distinct_user_card_pairs", "distinct_merchants", "users_with_fraud", "amount", "ordering", "elapsed_seconds",
    )}
    summary["report_path"] = str(target)
    summary["report_sha256"] = hashlib.sha256(content).hexdigest()
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
