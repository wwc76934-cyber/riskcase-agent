"""Cross-check initial validation metrics with independent SQL and queue counts."""

import argparse
from collections import Counter
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = json.loads(args.report.read_text(encoding="utf-8"))
    frozen_path = args.report.parent / "frozen-rules.json"
    queue_path = args.report.parent / "daily-review.jsonl"
    if report["final_test_evaluated"] or report["evaluation_split"] != "validation":
        raise ValueError("Validation-only result required")
    if digest(frozen_path) != report["frozen_rules_sha256"] or digest(queue_path) != report["daily_review_sha256"]:
        raise ValueError("Baseline artifacts changed")
    frozen = json.loads(frozen_path.read_text(encoding="utf-8"))
    manifest_path = Path(frozen["features_manifest"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    feature_path = manifest_path.parent / "features.sqlite"
    cohort_path = Path(manifest["cohort_report"])
    if digest(cohort_path) != manifest["cohort_report_sha256"]:
        raise ValueError("Cohort report changed")
    cohort = json.loads(cohort_path.read_text(encoding="utf-8"))
    label_path = cohort_path.parent / "dataset/evaluation-labels.sqlite"
    if digest(feature_path) != frozen["features_sha256"] or digest(label_path) != cohort["artifact_sha256"]["dataset/evaluation-labels.sqlite"]:
        raise ValueError("Feature/label source changed")
    with closing(sqlite3.connect(feature_path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("ATTACH DATABASE ? AS evaluation", (label_path.resolve().as_uri() + "?mode=ro",))
        limits = report["thresholds"]
        # SQL independently reconstructs predicates; does not import rule code.
        actual = db.execute("""WITH flags AS (
            SELECT l.is_fraud y,(f.amount_cents>?) big,(f.count_1h>?) frequent,
            (f.has_history_30d=1 AND f.merchant_seen_30d=0) unseen
            FROM features f JOIN evaluation.labels l
            ON f.transaction_id=l.transaction_id AND f.split=l.split WHERE f.split='validation')
            SELECT COUNT(*),SUM(y),SUM(big),SUM(big*y),SUM((big+frequent+unseen)>=?),
            SUM(((big+frequent+unseen)>=?)*y) FROM flags""",
            (limits["large_positive_amount_cents"], limits["high_prior_count_1h"],
             limits["minimum_combined_hits"], limits["minimum_combined_hits"])).fetchone()
        expected = [report["strategies"]["amount_only"]["threshold_policy"][key]
                    for key in ("rows", "positives", "reviewed_or_alerted", "true_positives")]
        expected += [report["strategies"]["history_rules"]["threshold_policy"][key]
                     for key in ("reviewed_or_alerted", "true_positives")]
        if list(actual) != expected:
            raise ValueError("Independent SQL threshold counts disagree")
        daily_counts, totals, true_counts = Counter(), Counter(), Counter()
        for line in queue_path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if {"is_fraud", "label", "available_at"} & row.keys():
                raise ValueError("Review queue leaked evaluation answers")
            strategy = row["strategy"]
            daily_counts[strategy, row["event_time"][:10]] += 1
            totals[strategy] += 1
            outcome = db.execute("SELECT is_fraud FROM evaluation.labels WHERE transaction_id=? AND split='validation'", (row["transaction_id"],)).fetchone()
            if outcome is None:
                raise ValueError("Queued record outside validation")
            true_counts[strategy] += outcome[0]
        if any(n > frozen["policy"]["daily_review_budget"] for n in daily_counts.values()):
            raise ValueError("Daily review budget exceeded")
        for strategy in report["strategies"]:
            metric = report["strategies"][strategy]["daily_review"]
            if totals[strategy] != metric["reviewed_or_alerted"] or true_counts[strategy] != metric["true_positives"]:
                raise ValueError("Review queue metrics disagree")
    print(json.dumps({"status": "passed", "evaluation_split": "validation", "final_test_read": False,
                      "threshold_counts_sql_verified": list(actual), "review_rows_verified": sum(totals.values()),
                      "report_sha256": digest(args.report), "audit_script_sha256": digest(Path(__file__)),
                      "limits": "SQL counts and exported queue checked; ranking/tie behavior also covered by unit tests."}, indent=2))


if __name__ == "__main__":
    main()
