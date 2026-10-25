"""Independently sort saved development-fold predictions and recompute counts/AP."""

import argparse
from collections import defaultdict
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3

import numpy as np
import pandas as pd

from scripts.verify_ml_run import digest, independent_ap


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    run = args.report.parent
    report = json.loads(args.report.read_text(encoding="utf-8"))
    if report["status"] != "temporal_diagnostics_completed" or report["read_partitions"] != ["train"] or report["final_test_evaluated"] or report["validation_2018_evaluated"]:
        raise ValueError("Development-only completed experiment required")
    for name, expected in report["artifact_sha256"].items():
        if digest(run / name) != expected:
            raise ValueError(f"Artifact changed: {name}")
    frozen = json.loads((run / "frozen-experiment.json").read_text(encoding="utf-8"))
    policy = frozen["policy"]
    manifest_path = Path(frozen["features_manifest"])
    if digest(manifest_path) != frozen["features_manifest_sha256"]:
        raise ValueError("Manifest changed")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    cohort_path = Path(manifest["cohort_report"])
    if digest(cohort_path) != manifest["cohort_report_sha256"]:
        raise ValueError("Cohort changed")
    features = manifest_path.parent / "features.sqlite"
    labels = cohort_path.parent / "dataset/evaluation-labels.sqlite"
    if digest(features) != frozen["features_sha256"] or digest(labels) != frozen["labels_sha256"]:
        raise ValueError("Dataset changed")
    checks, total_rows, total_queue = {}, 0, 0
    with closing(sqlite3.connect(features.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("ATTACH DATABASE ? AS evaluation", (labels.resolve().as_uri() + "?mode=ro",))
        for fold in policy["folds"]:
            name = fold["name"]
            facts = pd.read_sql_query("""SELECT f.transaction_id,f.user_id,f.event_time,l.is_fraud FROM features f
                JOIN evaluation.labels l ON f.transaction_id=l.transaction_id AND f.split=l.split
                WHERE f.split='train' AND f.event_time>=? AND f.event_time<?""", db,
                params=(fold["evaluate_start"], fold["evaluate_end"])).set_index("transaction_id")
            with np.load(run / f"{name}-scores.npz", allow_pickle=False) as archive:
                if set(archive.files) != {"transaction_id", "user_id", "event_time", *policy["variants"]}:
                    raise ValueError("Unexpected score columns")
                ids = archive["transaction_id"]
                if len(ids) != len(set(ids)) or len(ids) != len(facts) or set(ids) != set(facts.index):
                    raise ValueError("Prediction IDs disagree with exact evaluation window")
                facts = facts.reindex(ids)
                for column in ("event_time", "user_id"):
                    if not np.array_equal(archive[column], facts[column].to_numpy()):
                        raise ValueError("Prediction metadata changed")
                y = facts["is_fraud"].to_numpy(dtype=np.int64)
                fold_stats = report["folds"][name]
                if len(y) != fold_stats["evaluate_rows"] or int(y.sum()) != fold_stats["evaluate_positives"]:
                    raise ValueError("Fold counts disagree")
                queues = defaultdict(list)
                for line in (run / f"{name}-review.jsonl").read_text(encoding="utf-8").splitlines():
                    row = json.loads(line)
                    if {"label", "is_fraud", "available_at"} & row.keys():
                        raise ValueError("Answers in exported review queue")
                    queues[row["variant"]].append(row)
                if set(queues) != set(policy["variants"]):
                    raise ValueError("Missing or unexpected variant")
                days = facts["event_time"].str[:10].to_numpy()
                indices_by_day = [np.flatnonzero(days == day) for day in sorted(set(days))]
                ties = [hashlib.sha256(f"{policy['tie_seed']}\0{identity}".encode()).hexdigest() for identity in ids]
                checks[name] = {}
                for variant in policy["variants"]:
                    scores = archive[variant]
                    if not np.isfinite(scores).all():
                        raise ValueError("Nonfinite score")
                    chosen, ranks = [], []
                    for indices in indices_by_day:
                        top = sorted(indices, key=lambda i: (scores[i], ties[i], ids[i]), reverse=True)[:policy["daily_review_budget"]]
                        chosen.extend(top)
                        ranks.extend(range(1, len(top)+1))
                    queue, stats = queues[variant], fold_stats["variants"][variant]
                    if [row["transaction_id"] for row in queue] != ids[chosen].tolist() or [row["day_rank"] for row in queue] != ranks:
                        raise ValueError("Independent ranking disagrees")
                    if not np.array_equal([row["score"] for row in queue], scores[chosen]):
                        raise ValueError("Exported queue scores disagree")
                    tp, alerts, positives = int(y[chosen].sum()), len(chosen), int(y.sum())
                    values = {"true_positives": tp, "false_positives": alerts-tp,
                        "false_negatives": positives-tp, "reviewed_or_alerted": alerts,
                        "precision": tp/alerts, "recall": tp/positives if positives else None}
                    for key, value in values.items():
                        stored = stats["daily_review"][key]
                        if (stored is not None if value is None else stored is None or not np.isclose(stored, value, rtol=0, atol=1e-12)):
                            raise ValueError(f"Independent count disagrees: {name}/{variant}/{key}")
                    ap = independent_ap(y, scores) if positives else None
                    if (stats["average_precision"] is not None if ap is None else not np.isclose(stats["average_precision"], ap, rtol=0, atol=1e-12)):
                        raise ValueError("Independent AP disagrees")
                    checks[name][variant] = {"AP": ap, **values}
                    total_queue += alerts
                total_rows += len(y)
    result = {"status": "passed", "report_sha256": digest(args.report),
        "verification_script_sha256": digest(Path(__file__)), "read_partitions": ["train"],
        "evaluation_rows_verified": total_rows, "review_rows_verified": total_queue, "checks": checks,
        "limits": "Artifact and metric consistency only; not significance or real-world validity."}
    (run / "verification.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
