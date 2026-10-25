"""Replay MLP development predictions and check stopping/checkpoint evidence.

Only load locally produced trusted artifacts, not reports/joblib from outsiders.
"""

import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3

import joblib
import numpy as np
import pandas as pd
import torch

from app.risk.neural_model import RiskMLP, dense_float32, make_loader, predict_batches, seed_training
from scripts.verify_ml_run import digest, independent_ap


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    run = args.report.parent
    report = json.loads(args.report.read_text(encoding="utf-8"))
    if report["status"] != "mlp_development_evaluated" or report["final_test_evaluated"]:
        raise ValueError("Completed development-only MLP result required")
    for name, expected in report["artifact_sha256"].items():
        if digest(run / name) != expected:
            raise ValueError(f"Artifact changed: {name}")
    frozen = json.loads((run / "frozen-experiment.json").read_text(encoding="utf-8"))
    policy = frozen["policy"]
    curves = [json.loads(line) for line in (run / "curves.jsonl").read_text(encoding="utf-8").splitlines()]
    selection = [row for row in curves if row["phase"] == "selection"]
    refit = [row for row in curves if row["phase"] == "refit"]
    if len(selection) != report["selection"]["epochs_run"] or len(selection) > policy["max_epochs"]:
        raise ValueError("Epoch curve length disagrees")
    best, chosen, bad = -np.inf, 0, 0
    for epoch, row in enumerate(selection, 1):
        if row["epoch"] != epoch or not np.isfinite(row["selection_AP"]):
            raise ValueError("Invalid epoch sequence")
        improved = row["selection_AP"] > best + policy["early_stopping"]["minimum_improvement"]
        if improved:
            best, chosen, bad = row["selection_AP"], epoch, 0
        else:
            bad += 1
        if row["new_best"] != improved or (bad >= policy["early_stopping"]["patience"] and epoch != len(selection)):
            raise ValueError("Stopping protocol disagrees")
        for key in ("train_eval_weighted_loss", "train_AP", "selection_weighted_loss"):
            if not np.isfinite(row[key]):
                raise ValueError("Nonfinite training curve")
    if chosen != report["selection"]["selected_epoch"] or chosen != report["refit"]["epochs"]:
        raise ValueError("Selected/refit epoch count disagrees")
    if len(selection) < policy["max_epochs"] and bad < policy["early_stopping"]["patience"]:
        raise ValueError("Training stopped without declared criterion")
    if [row["epoch"] for row in refit] != list(range(1, chosen+1)):
        raise ValueError("Refit curve sequence disagrees")
    for row in curves:
        expected_rows = report["selection"]["fit_rows"] if row["phase"] == "selection" else report["refit"]["rows"]
        updates = row["training_updates"]
        if updates["rows"] != expected_rows or updates["batches"] != (expected_rows+policy["batch_size"]-1)//policy["batch_size"]:
            raise ValueError("Epoch skipped or duplicated batch rows")
    manifest_path = Path(frozen["features_manifest"])
    if digest(manifest_path) != frozen["features_manifest_sha256"]:
        raise ValueError("Manifest changed")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    cohort_path = Path(manifest["cohort_report"])
    if digest(cohort_path) != manifest["cohort_report_sha256"]:
        raise ValueError("Cohort changed")
    features, labels = manifest_path.parent / "features.sqlite", cohort_path.parent / "dataset/evaluation-labels.sqlite"
    if digest(features) != frozen["features_sha256"] or digest(labels) != frozen["labels_sha256"]:
        raise ValueError("Dataset changed")
    baseline_path = Path(frozen["baseline_report"])
    if digest(baseline_path) != frozen["baseline_report_sha256"]:
        raise ValueError("Baseline report changed")
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    for name, expected in report["baseline_comparison"].items():
        source = baseline["strategies"][name]
        if expected != {"AP": source["average_precision"], "caught": source["daily_review"]["true_positives"], "reviewed": source["daily_review"]["reviewed_or_alerted"]}:
            raise ValueError("Historical comparison changed")
    with closing(sqlite3.connect(features.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("ATTACH DATABASE ? AS evaluation", (labels.resolve().as_uri() + "?mode=ro",))
        facts = pd.read_sql_query("""SELECT f.transaction_id,f.user_id,f.event_time,l.is_fraud FROM features f
            JOIN evaluation.labels l ON f.transaction_id=l.transaction_id AND f.split=l.split
            WHERE f.split='validation'""", db).set_index("transaction_id")
        with np.load(run / "validation-scores.npz", allow_pickle=False) as archive:
            if set(archive.files) != {"transaction_id", "user_id", "event_time", "mlp"}:
                raise ValueError("Unexpected prediction fields")
            ids, scores = archive["transaction_id"], archive["mlp"]
            if len(set(ids)) != len(ids) or set(ids) != set(facts.index) or not np.isfinite(scores).all():
                raise ValueError("Invalid prediction coverage/scores")
            facts = facts.reindex(ids)
            if not np.array_equal(archive["event_time"], facts["event_time"].to_numpy()) or not np.array_equal(archive["user_id"], facts["user_id"].to_numpy()):
                raise ValueError("Prediction metadata disagrees")
            y = facts["is_fraud"].to_numpy(dtype=np.int64)
            if len(y) != report["validation_rows"] or int(y.sum()) != report["validation_positives"]:
                raise ValueError("Validation counts disagree")
            ties = [hashlib.sha256(f"{policy['tie_seed']}\0{identity}".encode()).hexdigest() for identity in ids]
            days = facts["event_time"].str[:10].to_numpy()
            chosen_ids, day_ranks = [], []
            for day in sorted(set(days)):
                indices = np.flatnonzero(days == day)
                selected = sorted(indices, key=lambda i: (scores[i], ties[i], ids[i]), reverse=True)[:policy["daily_review_budget"]]
                chosen_ids.extend(selected)
                day_ranks.extend(range(1, len(selected)+1))
            queue = [json.loads(line) for line in (run / "daily-review.jsonl").read_text(encoding="utf-8").splitlines()]
            if any({"label", "is_fraud", "available_at"} & row.keys() for row in queue):
                raise ValueError("Queue leaked evaluation answers")
            if [row["transaction_id"] for row in queue] != ids[chosen_ids].tolist() or [row["day_rank"] for row in queue] != day_ranks:
                raise ValueError("Independent daily sort disagrees")
            if not np.array_equal([row["score"] for row in queue], scores[chosen_ids]):
                raise ValueError("Queue scores disagree")
            tp, count, positives = int(y[chosen_ids].sum()), len(chosen_ids), int(y.sum())
            values = {"true_positives": tp, "false_positives": count-tp, "false_negatives": positives-tp,
                "reviewed_or_alerted": count, "precision": tp/count, "recall": tp/positives}
            for key, value in values.items():
                if not np.isclose(value, report["metrics"]["daily_review"][key], rtol=0, atol=1e-12):
                    raise ValueError(f"Metric disagrees: {key}")
            ap = independent_ap(y, scores)
            if not np.isclose(ap, report["metrics"]["average_precision"], rtol=0, atol=1e-12):
                raise ValueError("Independent AP disagrees")
            sample = sorted(set(np.random.default_rng(42).choice(len(ids), 128, replace=False)) | set(np.argsort(scores)[-16:]))
            sample_ids = ids[sample].tolist()
            columns = list(frozen["raw_model_columns"])
            marks = ",".join("?" for _ in sample_ids)
            frame = pd.read_sql_query(f"SELECT transaction_id,{','.join(columns)} FROM features WHERE split='validation' AND transaction_id IN ({marks})", db, params=sample_ids)
            frame = frame.set_index("transaction_id").reindex(sample_ids)[columns]
            seed_training(policy["random_seed"], policy["threads"])
            prep = joblib.load(run / "refit-preprocess.joblib")  # Own, hash-verified artifact only.
            model_X = dense_float32(prep.transform(frame), max_bytes=policy["max_dense_bytes"])
            architecture = json.loads((run / "architecture.json").read_text(encoding="utf-8"))
            model = RiskMLP(architecture["refit_input_width"], architecture["hidden_widths"], architecture["dropout"])
            model.load_state_dict(torch.load(run / "refit-model.pt", map_location="cpu", weights_only=True))
            actual, _ = predict_batches(model, make_loader(model_X, batch_size=policy["batch_size"]))
            maximum = float(np.max(np.abs(actual - scores[sample])))
            if maximum > 1e-6:
                raise ValueError("Reload predictions disagree")
    result = {"status": "passed", "report_sha256": digest(args.report), "verification_script_sha256": digest(Path(__file__)),
        "final_test_evaluated": False, "validation_rows_verified": len(y), "review_rows_verified": count,
        "independent_AP": ap, "independent_daily_metrics": values,
        "selection_epochs_verified": len(selection), "refit_epochs_verified": chosen,
        "reload_check": {"rows": len(sample), "max_absolute_difference": maximum, "tolerance": 1e-6},
        "limits": "Artifact, protocol and arithmetic consistency; not real-world effectiveness or independent data ground truth."}
    (run / "verification.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
