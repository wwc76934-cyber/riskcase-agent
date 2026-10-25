"""Scan the verified full archive and retain an explicitly scoped history."""

from __future__ import annotations

import argparse
from collections import Counter
import codecs
import csv
from datetime import datetime
import json
from pathlib import Path
import tarfile
import time
from uuid import uuid4

from app.cli.cache_archive import ensure_archive
from app.risk.data_contract import REQUIRED_COLUMNS, parse_tabformer_row
from app.risk.history import Coverage, create_history_snapshot
from scripts.probe_tabformer import ARCHIVE_SHA256_DECLARED, SOURCE_COMMIT


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--users", default="0,1,2,3,4,5,6,7")
    parser.add_argument("--start", default="2019-12-01T00:00:00")
    parser.add_argument("--end", default="2020-01-01T00:00:00")
    args = parser.parse_args()
    try:
        users = tuple(user.strip() for user in args.users.split(","))
        coverage = Coverage(
            users, datetime.fromisoformat(args.start), datetime.fromisoformat(args.end),
            f"tabformer:{ARCHIVE_SHA256_DECLARED}", True, True,
        )
        if len(users) > 16 or (coverage.end - coverage.start).days > 120:
            raise ValueError("research snapshot budget: up to 16 users and 120 days")
    except ValueError as exc:
        parser.error(str(exc))

    archive_path = ensure_archive()
    output = Path(__file__).resolve().parents[2] / "data" / "history" / f"snapshot-{uuid4().hex[:12]}"
    output.mkdir(parents=True, exist_ok=False)
    user_set = set(users)
    counters = {"scanned_rows": 0, "selected_rows": 0, "source_csv_fully_scanned": False}
    label_counts = Counter()
    started = time.monotonic()

    def selected_transactions():
        # Labels go to a separate artifact, not the SQLite database queried by tools.
        with (output / "evaluation-labels.jsonl").open("x", encoding="utf-8", newline="\n") as label_output:
            with tarfile.open(archive_path, mode="r|gz") as archive:
                for member in archive:
                    if member.name != "card_transaction.v1.csv" or not member.isfile():
                        continue
                    handle = archive.extractfile(member)
                    if handle is None:
                        raise ValueError("CSV not readable")
                    with codecs.getreader("utf-8-sig")(handle) as text:
                        reader = csv.DictReader(text)
                        if not REQUIRED_COLUMNS.issubset(reader.fieldnames or []):
                            raise ValueError("CSV schema mismatch")
                        for row_number, row in enumerate(reader, start=1):
                            counters["scanned_rows"] = row_number
                            if row_number % 2000000 == 0:
                                print(f"Scanned {row_number:,} rows", flush=True)
                            if row.get("User") not in user_set:
                                continue
                            # Parse selected users even outside the retained dates;
                            # malformed dates must not silently disappear from coverage.
                            tx, label = parse_tabformer_row(
                                row, source_id=coverage.source_id, source_row=row_number
                            )
                            if not coverage.start <= tx.event_time < coverage.end:
                                continue
                            counters["selected_rows"] += 1
                            label_counts[str(label.is_fraud)] += 1
                            label_output.write(json.dumps({
                                "transaction_id": label.transaction_id, "is_fraud": label.is_fraud,
                                "available_at": None,
                            }, sort_keys=True) + "\n")
                            yield tx
                    counters["source_csv_fully_scanned"] = True
                    return
                raise ValueError("Expected CSV member not found")

    try:
        manifest_path = create_history_snapshot(output / "facts", selected_transactions(), coverage)
        report = {
            "status": "ready", "source_commit": SOURCE_COMMIT,
            "archive_sha256_verified": ARCHIVE_SHA256_DECLARED,
            "history_manifest": str(manifest_path), **counters,
            "evaluation_only_label_counts": dict(label_counts),
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "limits": [
                "Complete only within registered users/dates in a synthetic source.",
                "No real label or ingestion availability established.",
                "Business duplicate transaction identity is not established.",
            ],
        }
    except Exception as exc:
        report = {"status": "failed", "error_type": type(exc).__name__, "error": str(exc), **counters}
    (output / "build-report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    if report["status"] == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
