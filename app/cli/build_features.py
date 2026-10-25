"""Build label-free feature rows from the completed cohort facts only."""

from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import subprocess
import time
from uuid import uuid4

from app.cli.cache_archive import file_sha256
from app.risk.cohort import CohortPlan
from app.risk.features import CATEGORICAL_FEATURES, METADATA, NUMERIC_FEATURES, FeatureTransaction, iter_history_features


def main():
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cohort-report", type=Path, default=root / "data/cohorts/cohort-3bfdbaab486f/build-report.json")
    args = parser.parse_args()
    report = json.loads(args.cohort_report.read_text(encoding="utf-8"))
    if report.get("status") != "research_snapshot_built" or not report.get("source_csv_fully_scanned") or not report.get("class_counts_sufficient"):
        raise ValueError("Completed eligible cohort required")
    plan = CohortPlan(**report["coverage"])
    source = args.cohort_report.parent / "dataset/facts.sqlite"
    if file_sha256(source) != report["artifact_sha256"]["dataset/facts.sqlite"]:
        raise ValueError("Facts hash mismatch")
    output = root / "data/features" / f"features-{uuid4().hex[:12]}"
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    code_files = ("app/risk/features.py", "app/cli/build_features.py")
    lineage = {"cohort_report": str(args.cohort_report.resolve()), "cohort_report_sha256": file_sha256(args.cohort_report),
               "facts_sha256": report["artifact_sha256"]["dataset/facts.sqlite"],
               "code_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
               "code_file_sha256": {name: file_sha256(root / name) for name in code_files}}
    counters = Counter()
    empty = Counter()
    source_rows = 0
    try:
        with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as facts, closing(sqlite3.connect(output / "features.sqlite")) as target:
            # Explicit columns. There is no label database connection.
            cursor = facts.execute("""SELECT transaction_id,user_id,event_time,amount_cents,
                merchant_id,channel,mcc,split FROM transactions ORDER BY user_id,event_time,source_row""")
            def transactions():
                nonlocal source_rows
                for row in cursor:
                    event_time = datetime.fromisoformat(row[2])
                    if plan.partition(event_time) != row[7]:
                        raise ValueError("Stored split disagrees with frozen event-time boundary")
                    source_rows += 1
                    yield FeatureTransaction(row[0], row[1], event_time, *row[3:])
            fields = (*METADATA, *NUMERIC_FEATURES, *CATEGORICAL_FEATURES)
            declarations = [f"{name} TEXT" for name in METADATA]
            declarations += [f"{name} INTEGER" for name in NUMERIC_FEATURES]
            declarations += [f"{name} TEXT" for name in CATEGORICAL_FEATURES]
            target.execute(f"CREATE TABLE features ({','.join(declarations)}, PRIMARY KEY(transaction_id))")
            insert = f"INSERT INTO features ({','.join(fields)}) VALUES ({','.join('?' for _ in fields)})"
            for row in iter_history_features(transactions(), coverage_start=datetime.fromisoformat(plan.warmup_start)):
                if not row["history_30d_complete"]:
                    raise ValueError("Scored row lacks 30-day source coverage")
                target.execute(insert, tuple(row[name] for name in fields))
                counters[row["split"]] += 1
                empty[row["split"]] += int(not row["has_history_30d"])
                if sum(counters.values()) % 250000 == 0:
                    print(f"Built {sum(counters.values()):,} feature rows", flush=True)
            if source_rows != report["selected_rows"]:
                raise ValueError("Source row count mismatch")
            if any(counters[split] != report["partitions"][split]["rows"] for split in ("train", "validation", "test")):
                raise ValueError("Feature partition counts disagree with cohort")
            target.execute("CREATE INDEX feature_split ON features(split)")
            target.commit()
        manifest = {"status": "features_built", "created_at": datetime.now(timezone.utc).isoformat(),
                    "feature_version": 1, **lineage, "coverage": report["coverage"],
                    "source_rows_processed": source_rows, "feature_rows": sum(counters.values()),
                    "rows_by_split": dict(counters), "rows_without_previous_30d_events": dict(empty),
                    "numeric_features": NUMERIC_FEATURES, "categorical_features": CATEGORICAL_FEATURES,
                    "metadata_not_model_features": METADATA, "labels_read": False,
                    "amount_unit": "integer 0.01 source units; currency unspecified",
                    "history_rule": "[t-window,t), exclude all same-time records; source-local time",
                    "features_sha256": file_sha256(output / "features.sqlite"),
                    "elapsed_seconds": round(time.monotonic() - started, 3),
                    "limitations": ["Synthetic retrospective event-time experiment; no ingestion availability proof.",
                                    "Historical validation/test transactions may supply later observed history, never labels.",
                                    "merchant_seen refers to source identifier, not a physical shop.",
                                    "Unknown previous time remains NULL; zero history is not a normality verdict."]}
        (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    except Exception as exc:
        (output / "failed.json").write_text(json.dumps({"status": "failed", "error": str(exc), **lineage}, indent=2) + "\n", encoding="utf-8")
        raise
    print(json.dumps({"output_directory": str(output), **manifest}, indent=2), flush=True)


if __name__ == "__main__":
    main()
