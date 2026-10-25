"""Aggregate train/validation diagnostics; no tuning, final-test or case labels exported."""

import argparse
from collections import Counter
from contextlib import closing
import json
from pathlib import Path
import sqlite3

import numpy as np
import pandas as pd

from scripts.verify_ml_run import digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = json.loads(args.report.read_text(encoding="utf-8"))
    run = args.report.parent
    verification = json.loads((run / "verification.json").read_text(encoding="utf-8"))
    if verification["status"] != "passed" or verification["report_sha256"] != digest(args.report):
        raise ValueError("Verify this exact local run first")
    for name, expected in report["artifact_sha256"].items():
        if digest(run / name) != expected:
            raise ValueError("Run artifacts changed")
    frozen = json.loads((run / "frozen-experiment.json").read_text(encoding="utf-8"))
    path = Path(frozen["features_manifest"])
    if digest(path) != frozen["features_manifest_sha256"]:
        raise ValueError("Manifest changed")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    cohort_path = Path(manifest["cohort_report"])
    if digest(cohort_path) != manifest["cohort_report_sha256"]:
        raise ValueError("Cohort changed")
    labels = cohort_path.parent / "dataset/evaluation-labels.sqlite"
    features = path.parent / "features.sqlite"
    if digest(features) != frozen["features_sha256"] or digest(labels) != frozen["labels_sha256"]:
        raise ValueError("Dataset changed")
    with closing(sqlite3.connect(features.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("ATTACH DATABASE ? AS evaluation", (labels.resolve().as_uri() + "?mode=ro",))
        db.execute("CREATE TEMP TABLE selections(strategy TEXT, transaction_id TEXT, PRIMARY KEY(strategy,transaction_id))")
        queue = [json.loads(line) for line in (run / "daily-review.jsonl").read_text(encoding="utf-8").splitlines()]
        db.executemany("INSERT INTO selections VALUES (?,?)", [(row["strategy"], row["transaction_id"]) for row in queue])
        db.execute("""CREATE TEMP VIEW development AS SELECT f.*,l.is_fraud FROM features f
            JOIN evaluation.labels l ON f.transaction_id=l.transaction_id AND f.split=l.split
            WHERE f.split IN ('train','validation')""")
        channels = pd.read_sql_query("""SELECT split,channel,COUNT(*) rows,SUM(is_fraud) positives
            FROM development GROUP BY split,channel ORDER BY split,channel""", db).to_dict("records")
        monthly = pd.read_sql_query("""SELECT substr(event_time,1,7) month,COUNT(*) rows,SUM(is_fraud) positives
            FROM development WHERE split='validation' GROUP BY month ORDER BY month""", db).to_dict("records")
        selected_monthly = pd.read_sql_query("""SELECT s.strategy,substr(d.event_time,1,7) month,
            COUNT(*) reviewed,SUM(d.is_fraud) caught FROM development d JOIN selections s
            ON d.transaction_id=s.transaction_id WHERE d.split='validation'
            GROUP BY s.strategy,month ORDER BY s.strategy,month""", db).to_dict("records")
        concentration = {}
        for split in ("train", "validation"):
            per_user = [row[0] for row in db.execute("""SELECT SUM(is_fraud) n FROM development
                WHERE split=? GROUP BY user_id HAVING n>0 ORDER BY n DESC""", (split,))]
            concentration[split] = {"users_with_positive_labels": len(per_user),
                "positives": sum(per_user), "largest_user_positives": per_user[0],
                "top_5_users_positives": sum(per_user[:5])}
        outcomes = dict(db.execute("SELECT transaction_id,is_fraud FROM development WHERE split='validation'"))
        percentiles = {}
        with np.load(run / "validation-scores.npz", allow_pickle=False) as scores:
            y = np.asarray([outcomes[identity] for identity in scores["transaction_id"]])
            for name in frozen["policy"]["models"]:
                percentiles[name] = {str(label): dict(zip(("p50", "p90", "p99"),
                    np.quantile(scores[name][y == label], [.5, .9, .99]).tolist())) for label in (0, 1)}
        selected_ids = {name: {row["transaction_id"] for row in queue if row["strategy"] == name}
                        for name in report["strategies"]}
        tree, rules = selected_ids["hist_gradient_boosting"], selected_ids["history_rules"]
        overlap = {"both_selected": len(tree & rules),
            "both_selected_positive": sum(outcomes[key] for key in tree & rules),
            "tree_only_selected_positive": sum(outcomes[key] for key in tree-rules),
            "rules_only_selected_positive": sum(outcomes[key] for key in rules-tree)}
    result = {"report_sha256": digest(args.report), "analysis_script_sha256": digest(Path(__file__)),
        "final_test_read": False, "channel_counts": channels, "validation_months": monthly,
        "validation_review_by_month": selected_monthly, "positive_user_concentration": concentration,
        "validation_score_quantiles": percentiles, "tree_vs_rules_overlap": overlap,
        "limits": "Descriptive aggregates; no causal diagnosis, significance test or model tuning."}
    (run / "diagnostics.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
