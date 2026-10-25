"""Replay saved validation scores with independent sorting and AP arithmetic.

Run as python -m scripts.verify_ml_run --report runs/.../report.json.
Only use reports/models generated locally by this project: hashes are integrity
checks against that local report, not authentication of external pickle files.
"""

import argparse
from collections import defaultdict
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3

import joblib
import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def independent_ap(y, scores):
    order = np.argsort(-scores, kind="stable")
    ordered_scores, outcomes = scores[order], y[order]
    ends = np.flatnonzero(np.r_[ordered_scores[:-1] != ordered_scores[1:], True])
    true_counts = np.cumsum(outcomes)[ends]
    increments = np.diff(np.r_[0, true_counts]) / y.sum()
    return float(np.sum(increments * true_counts / (ends + 1)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    run = args.report.parent
    report = json.loads(args.report.read_text(encoding="utf-8"))
    if report["status"] != "validation_models_evaluated" or report["final_test_read"] is not False:
        raise ValueError("Completed validation-only report required")
    for name, expected in report["artifact_sha256"].items():
        if digest(run / name) != expected:
            raise ValueError(f"Artifact changed: {name}")
    frozen = json.loads((run / "frozen-experiment.json").read_text(encoding="utf-8"))
    policy = frozen["policy"]
    manifest_path = Path(frozen["features_manifest"])
    if digest(manifest_path) != frozen["features_manifest_sha256"]:
        raise ValueError("Feature manifest changed")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    cohort_path = Path(manifest["cohort_report"])
    if digest(cohort_path) != manifest["cohort_report_sha256"]:
        raise ValueError("Cohort report changed")
    feature_path = manifest_path.parent / "features.sqlite"
    labels_path = cohort_path.parent / "dataset/evaluation-labels.sqlite"
    if digest(feature_path) != frozen["features_sha256"] or digest(labels_path) != frozen["labels_sha256"]:
        raise ValueError("Dataset changed")
    with closing(sqlite3.connect(feature_path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("ATTACH DATABASE ? AS evaluation", (labels_path.resolve().as_uri() + "?mode=ro",))
        facts = pd.read_sql_query("""SELECT f.transaction_id,f.event_time,l.is_fraud
            FROM features f JOIN evaluation.labels l
            ON f.transaction_id=l.transaction_id AND f.split=l.split
            WHERE f.split='validation' ORDER BY f.transaction_id""", db).set_index("transaction_id")
        with np.load(run / "validation-scores.npz", allow_pickle=False) as archive:
            expected_arrays = {"transaction_id", "user_id", "event_time", *report["strategies"]}
            if set(archive.files) != expected_arrays:
                raise ValueError("Score archive has unexpected columns")
            ids, times = archive["transaction_id"], archive["event_time"]
            if len(ids) != len(facts) or len(set(ids)) != len(ids) or set(ids) != set(facts.index):
                raise ValueError("Saved predictions do not cover validation exactly")
            facts = facts.reindex(ids)
            if not np.array_equal(times, facts["event_time"].to_numpy()):
                raise ValueError("Saved transaction timestamps disagree")
            y = facts["is_fraud"].to_numpy(dtype=np.int64)
            if len(y) != report["validation_rows"] or int(y.sum()) != report["validation_positives"]:
                raise ValueError("Validation counts disagree")
            queues = defaultdict(list)
            for line in (run / "daily-review.jsonl").read_text(encoding="utf-8").splitlines():
                row = json.loads(line)
                if {"label", "is_fraud", "available_at"} & row.keys():
                    raise ValueError("Queue leaked labels")
                queues[row["strategy"]].append(row)
            if set(queues) != set(report["strategies"]):
                raise ValueError("Strategy queues disagree")
            days = np.asarray([stamp[:10] for stamp in times])
            # A full sort per day, independent of the training runner's heap.
            day_indices = [np.flatnonzero(days == day) for day in sorted(set(days))]
            ties = [hashlib.sha256(f"{policy['tie_seed']}\0{identity}".encode()).hexdigest() for identity in ids]
            checks = {}
            sample = set(np.random.default_rng(42).choice(len(ids), size=64, replace=False).tolist())
            for name, stats in report["strategies"].items():
                scores = archive[name]
                if not np.isfinite(scores).all():
                    raise ValueError("Nonfinite saved scores")
                selected = []
                ranks = []
                for indices in day_indices:
                    ranked = sorted(indices, key=lambda i: (scores[i], ties[i], ids[i]), reverse=True)[:policy["daily_review_budget"]]
                    selected.extend(ranked)
                    ranks.extend(range(1, len(ranked) + 1))
                queue = queues[name]
                if [row["transaction_id"] for row in queue] != ids[selected].tolist() or [row["day_rank"] for row in queue] != ranks:
                    raise ValueError(f"Independent daily sorting disagrees: {name}")
                if not np.array_equal(np.asarray([row["score"] for row in queue]), scores[selected]):
                    raise ValueError("Queue score values disagree")
                tp, alerts, positives = int(y[selected].sum()), len(selected), int(y.sum())
                actual = dict(true_positives=tp, false_positives=alerts-tp,
                    false_negatives=positives-tp, reviewed_or_alerted=alerts,
                    precision=tp/alerts, recall=tp/positives)
                for key, value in actual.items():
                    if not np.isclose(value, stats["daily_review"][key], rtol=0, atol=1e-12):
                        raise ValueError(f"Independent metric disagrees: {name}/{key}")
                ap = independent_ap(y, scores)
                if not np.isclose(ap, stats["average_precision"], rtol=0, atol=1e-12):
                    raise ValueError(f"Independent AP disagrees: {name}")
                checks[name] = {"AP": ap, **actual}
                if name in policy["models"]:
                    sample.update(np.argsort(scores)[-16:].tolist())
            sample = sorted(sample)
            sample_ids = ids[sample].tolist()
            columns = frozen["model_input_columns"]
            placeholders = ",".join("?" for _ in sample_ids)
            frame = pd.read_sql_query(f"SELECT transaction_id,{','.join(columns)} FROM features WHERE split='validation' AND transaction_id IN ({placeholders})", db, params=sample_ids)
            frame = frame.set_index("transaction_id").reindex(sample_ids)[columns]
            prediction_checks = {}
            with threadpool_limits(limits=policy["threads"]):
                for name in policy["models"]:
                    model_path = run / f"{name}.joblib"
                    if digest(model_path) != report["strategies"][name]["model_sha256"]:
                        raise ValueError("Model changed")
                    model = joblib.load(model_path)  # Locally generated artifacts only.
                    predictions = model.predict_proba(frame)[:, 1]
                    maximum = float(np.max(np.abs(predictions - archive[name][sample])))
                    if maximum > 1e-12:
                        raise ValueError(f"Reloaded predictions disagree: {name}")
                    prediction_checks[name] = {"rows": len(sample), "maximum_absolute_difference": maximum}
    result = {"status": "passed", "final_test_read": False,
        "report_sha256": digest(args.report), "verification_script_sha256": digest(Path(__file__)),
        "validation_rows_verified": len(y), "review_rows_verified": sum(len(q) for q in queues.values()),
        "independent_metrics": checks, "reloaded_prediction_checks": prediction_checks,
        "limits": "Artifact replay and model reload checks; not independent dataset ground truth or model generalization proof."}
    (run / "verification.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
