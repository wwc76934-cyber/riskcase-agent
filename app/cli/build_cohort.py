"""Build one frozen research cohort from a verified local archive; no network."""

from __future__ import annotations

import argparse
import codecs
import csv
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import tarfile
import time
from uuid import uuid4

from app.cli.cache_archive import ARCHIVE_BYTES, file_sha256
from app.risk.cohort import MIN_CLASS_COUNTS, CohortPlan, user_catalog_sha256
from app.risk.cohort_build import build_cohort
from app.risk.data_contract import REQUIRED_COLUMNS
from scripts.probe_tabformer import ARCHIVE_SHA256_DECLARED, SOURCE_COMMIT


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, help="Equivalent regenerated full-source profile; identity catalog must match")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    plan = CohortPlan()
    contract = json.loads((root / "config/cohort-v1.json").read_text(encoding="utf-8"))
    if contract["plan"] != asdict(plan):
        raise ValueError("Code plan differs from frozen contract")
    if contract["class_count_minimum_per_scored_partition"] != MIN_CLASS_COUNTS:
        raise ValueError("Class-count gate differs from frozen contract")
    inventory_path = args.inventory if args.inventory is not None else root / contract["inventory_path"]
    inventory_hash = file_sha256(inventory_path)
    if args.inventory is None and inventory_hash != contract["inventory_sha256"]:
        raise ValueError("Inventory hash mismatch")
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    if inventory["archive_sha256_verified"] != ARCHIVE_SHA256_DECLARED or not inventory["source_csv_fully_scanned"]:
        raise ValueError("Inventory not from the expected complete source")
    if user_catalog_sha256(inventory["by_user"].keys()) != contract["user_catalog_sha256"]:
        raise ValueError("Identity catalog changed; do not silently change the cohort")
    # Consume ONLY the identity keys, not per-user positive counts.
    users = plan.select_users(inventory["by_user"].keys())
    archive_path = root / "data/raw/tabformer" / f"{ARCHIVE_SHA256_DECLARED}.tgz"
    if archive_path.stat().st_size != ARCHIVE_BYTES or file_sha256(archive_path) != ARCHIVE_SHA256_DECLARED:
        raise ValueError("Local archive mismatch")
    output = root / "data/cohorts" / f"cohort-{uuid4().hex[:12]}"
    output.mkdir(parents=True, exist_ok=False)
    code_files = ("app/risk/cohort.py", "app/risk/cohort_build.py", "app/cli/build_cohort.py", "app/risk/data_contract.py")
    frozen = {"contract": contract, "selected_users": users, "source_commit": SOURCE_COMMIT,
              "effective_inventory_path": str(inventory_path.resolve()), "effective_inventory_sha256": inventory_hash,
              "archive_sha256_verified": ARCHIVE_SHA256_DECLARED,
              "code_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
              "code_file_sha256": {name: file_sha256(root / name) for name in code_files}}
    (output / "frozen-plan.json").write_text(json.dumps(frozen, indent=2) + "\n", encoding="utf-8")
    counters = {"source_csv_fully_scanned": False}
    started = time.monotonic()

    def records():
        with tarfile.open(archive_path, mode="r|gz") as archive:
            for member in archive:
                if member.name != "card_transaction.v1.csv" or not member.isfile():
                    continue
                handle = archive.extractfile(member)
                if handle is None:
                    raise ValueError("CSV unreadable")
                with codecs.getreader("utf-8-sig")(handle) as text:
                    reader = csv.DictReader(text)
                    columns = reader.fieldnames or []
                    if not REQUIRED_COLUMNS.issubset(columns) or len(columns) != len(set(columns)):
                        raise ValueError("Source schema mismatch")
                    yield from enumerate(reader, start=1)
                counters["source_csv_fully_scanned"] = True
                return
            raise ValueError("Expected CSV missing")

    try:
        stats = build_cohort(output / "dataset", records(), users=users, plan=plan,
                             source_id=f"tabformer:{ARCHIVE_SHA256_DECLARED}",
                             progress=lambda n: print(f"Scanned {n:,} rows", flush=True))
        if not counters["source_csv_fully_scanned"] or stats["scanned_rows"] != inventory["total_rows"]:
            raise ValueError("Full source row count disagrees with inventory")
        report = {"status": "research_snapshot_built", "created_at": datetime.now(timezone.utc).isoformat(),
                  "data_kind": "synthetic", "coverage": asdict(plan), **stats, **counters,
                  "artifact_sha256": {name: file_sha256(output / name) for name in (
                      "frozen-plan.json", "dataset/facts.sqlite", "dataset/evaluation-labels.sqlite")},
                  "elapsed_seconds": round(time.monotonic() - started, 3),
                  "limitations": ["No real point-in-time/label availability proof.",
                                  "Fixed customer cohort; activity varies across splits. Report train-seen/unseen strata.",
                                  "Class-count gate is not independent-case coverage or statistical power.",
                                  "Duplicate candidates retained, not proven distinct business events.",
                                  "File separation is not a production permissions sandbox."]}
    except Exception as exc:
        report = {"status": "failed", "error_type": type(exc).__name__, "error": str(exc), **counters}
    (output / "build-report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output_directory": str(output), **report}, indent=2), flush=True)
    if report["status"] == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
