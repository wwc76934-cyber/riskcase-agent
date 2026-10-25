"""Fit simple rules without labels, freeze them, evaluate validation only."""

import argparse
from contextlib import closing
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import subprocess
from uuid import uuid4

from app.cli.cache_archive import file_sha256
from app.risk.rule_baseline import evaluate_validation, fit_thresholds


def main():
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--features-manifest", type=Path, default=root / "data/features/features-b9692149f1c1/manifest.json")
    args = parser.parse_args()
    manifest = json.loads(args.features_manifest.read_text(encoding="utf-8"))
    if manifest.get("status") != "features_built" or manifest.get("labels_read") is not False:
        raise ValueError("Completed label-free feature snapshot required")
    features_path = args.features_manifest.parent / "features.sqlite"
    if file_sha256(features_path) != manifest["features_sha256"]:
        raise ValueError("Features hash mismatch")
    cohort_path = Path(manifest["cohort_report"])
    if file_sha256(cohort_path) != manifest["cohort_report_sha256"]:
        raise ValueError("Cohort report hash mismatch")
    cohort = json.loads(cohort_path.read_text(encoding="utf-8"))
    labels_path = cohort_path.parent / "dataset/evaluation-labels.sqlite"
    policy_path = root / "config/rule-baseline-v1.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    if policy["evaluation_split"] != "validation" or policy["combined_minimum_hits"] != 2 or policy["quantile_method"] != "nearest_rank":
        raise ValueError("Unsupported policy version/settings")
    output = root / "runs" / f"rule-baseline-{uuid4().hex[:12]}"
    output.mkdir(parents=True, exist_ok=False)
    with closing(sqlite3.connect(features_path.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        thresholds = fit_thresholds(connection, percentile=policy["percentile"])
        frozen = {"policy": policy, "thresholds": asdict(thresholds),
                  "features_manifest": str(args.features_manifest.resolve()), "features_sha256": manifest["features_sha256"],
                  "policy_sha256": file_sha256(policy_path),
                  "code_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
                  "code_file_sha256": {name: file_sha256(root / name) for name in ("app/risk/rule_baseline.py", "app/cli/rule_baseline.py")},
                  "frozen_at": datetime.now(timezone.utc).isoformat(), "labels_accessed_before_freeze": False}
        (output / "frozen-rules.json").write_text(json.dumps(frozen, indent=2) + "\n", encoding="utf-8")
        # Only after freezing do we verify/open the labels for independent evaluation.
        if file_sha256(labels_path) != cohort["artifact_sha256"]["dataset/evaluation-labels.sqlite"]:
            raise ValueError("Labels hash mismatch")
        connection.execute("ATTACH DATABASE ? AS evaluation", (labels_path.resolve().as_uri() + "?mode=ro",))
        connection.row_factory = sqlite3.Row
        cursor = connection.execute("""SELECT f.transaction_id,f.event_time,f.split,f.amount_cents,
            f.count_1h,f.has_history_30d,f.merchant_seen_30d,l.is_fraud FROM main.features f
            JOIN evaluation.labels l ON f.transaction_id=l.transaction_id AND f.split=l.split
            WHERE f.split='validation'""")
        def validation_records():
            for record in cursor:
                record = dict(record)
                if record["is_fraud"] not in (0, 1):
                    raise ValueError("Unsupported evaluation outcome")
                record["is_fraud"] = bool(record["is_fraud"])
                yield record
        metrics, queues = evaluate_validation(validation_records(), thresholds,
            daily_budget=policy["daily_review_budget"], tie_seed=policy["tie_seed"])
    expected = cohort["partitions"]["validation"]
    if any(stats["threshold_policy"]["rows"] != expected["rows"] or
           stats["threshold_policy"]["positives"] != expected["fraud_rows"] for stats in metrics["strategies"].values()):
        raise ValueError("Validation label join/count mismatch")
    queue_path = output / "daily-review.jsonl"
    with queue_path.open("x", encoding="utf-8", newline="\n") as handle:
        for strategy in sorted(queues):
            for row in queues[strategy]:
                handle.write(json.dumps(row, sort_keys=True) + "\n")
    report = {"status": "validation_baseline_evaluated", "data_kind": "synthetic",
              "evaluation_split": "validation", "final_test_evaluated": False,
              "frozen_rules_sha256": file_sha256(output / "frozen-rules.json"),
              "daily_review_sha256": file_sha256(queue_path), **metrics,
              "limits": ["Initial rules, not fraud probability or verified business policy.",
                         "Daily ranking uses a completed day's transactions; not real-time blocking.",
                         "Budgets are an experimental assumption. Below-threshold rows may fill capacity.",
                         "Tie ranking is deterministic and label-independent; coarse-score results may depend on ties.",
                         "Synthetic retrospective validation only; no final-test or real-world effectiveness claim."]}
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output_directory": str(output), **report}, indent=2))


if __name__ == "__main__":
    main()
