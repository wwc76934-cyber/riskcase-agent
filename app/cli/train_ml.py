"""One frozen classical ML experiment: train fit, validation audit, no final test."""

import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import subprocess
import time
from uuid import uuid4
import warnings

import joblib
import numpy as np
import pandas as pd
import scipy
import sklearn
from sklearn.exceptions import ConvergenceWarning
from sklearn.metrics import average_precision_score, log_loss
from threadpoolctl import threadpool_limits

from app.cli.cache_archive import file_sha256
from app.risk.ml_data import load_split
from app.risk.ml_models import MODEL_COLUMNS, make_model
from app.risk.ml_evaluation import evaluate_scores, error_groups


def main():
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--features-manifest", type=Path, default=root / "data/features/features-b9692149f1c1/manifest.json")
    parser.add_argument("--rules-report", type=Path, default=root / "runs/rule-baseline-ee0d2eaf75f9/report.json")
    args = parser.parse_args()
    policy_path = root / "config/ml-experiment-v1.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    if policy["fit_split"] != "train" or policy["evaluation_split"] != "validation" or policy["models"] != ["logistic_regression", "hist_gradient_boosting"]:
        raise ValueError("Unsupported experiment scope")
    manifest = json.loads(args.features_manifest.read_text(encoding="utf-8"))
    if manifest.get("status") != "features_built" or manifest.get("labels_read") is not False:
        raise ValueError("Completed label-free feature snapshot required")
    cohort_path = Path(manifest["cohort_report"])
    if file_sha256(cohort_path) != manifest["cohort_report_sha256"]:
        raise ValueError("Cohort report changed")
    cohort = json.loads(cohort_path.read_text(encoding="utf-8"))
    feature_path = args.features_manifest.parent / "features.sqlite"
    labels_path = cohort_path.parent / "dataset/evaluation-labels.sqlite"
    if file_sha256(feature_path) != manifest["features_sha256"] or file_sha256(labels_path) != cohort["artifact_sha256"]["dataset/evaluation-labels.sqlite"]:
        raise ValueError("Feature/label file changed")
    previous = json.loads(args.rules_report.read_text(encoding="utf-8"))
    previous_frozen_path = args.rules_report.parent / "frozen-rules.json"
    if file_sha256(previous_frozen_path) != previous["frozen_rules_sha256"]:
        raise ValueError("Previous rules changed")
    previous_frozen = json.loads(previous_frozen_path.read_text(encoding="utf-8"))
    if previous_frozen["features_sha256"] != manifest["features_sha256"] or any(
        policy[name] != previous_frozen["policy"][name] for name in ("daily_review_budget", "tie_seed")):
        raise ValueError("Baseline data/review budget mismatch")
    versions = {"numpy": np.__version__, "pandas": pd.__version__, "scipy": scipy.__version__,
                "scikit_learn": sklearn.__version__, "joblib": joblib.__version__}
    if sklearn.__version__ != "1.7.2":
        raise ValueError("Use pinned requirements-ml.txt for the initial experiment")
    output = root / "runs" / f"ml-v1-{uuid4().hex[:12]}"
    output.mkdir(parents=True, exist_ok=False)
    names = ("app/risk/ml_models.py", "app/risk/ml_data.py", "app/risk/ml_evaluation.py", "app/risk/rule_baseline.py", "app/cli/train_ml.py")
    frozen = {"policy": policy, "versions": versions, "model_input_columns": MODEL_COLUMNS,
              "features_manifest_sha256": file_sha256(args.features_manifest), "features_sha256": manifest["features_sha256"],
              "labels_sha256": cohort["artifact_sha256"]["dataset/evaluation-labels.sqlite"],
              "rules_report_sha256": file_sha256(args.rules_report), "features_manifest": str(args.features_manifest.resolve()),
              "code_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
              "code_file_sha256": {name: file_sha256(root / name) for name in names},
              "requirements_sha256": file_sha256(root / "requirements-ml.txt"),
              "frozen_at": datetime.now(timezone.utc).isoformat()}
    (output / "frozen-experiment.json").write_text(json.dumps(frozen, indent=2) + "\n", encoding="utf-8")
    started = time.monotonic()
    try:
        with closing(sqlite3.connect(feature_path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            db.execute("ATTACH DATABASE ? AS evaluation", (labels_path.resolve().as_uri() + "?mode=ro",))
            def read(split):
                expected = cohort["partitions"][split]
                return load_split(db, split, expected_rows=expected["rows"], expected_positives=expected["fraud_rows"])
            X_train, y_train, meta_train = read("train")
            X_val, y_val, meta_val = read("validation")
        print(f"Loaded train={len(y_train):,}, validation={len(y_val):,}; no final-test query", flush=True)
        train_users = set(meta_train["user_id"])
        counts = np.bincount(y_train, minlength=2)
        class_weights = len(y_train) / (2 * counts)
        train_weights = class_weights[y_train]
        reports, queues, score_arrays = {}, {}, {}
        old_thresholds = previous["thresholds"]
        baseline_scores = {
            "amount_only": np.maximum(0, X_val["amount_cents"].to_numpy()),
            "history_rules": ((X_val["amount_cents"].to_numpy() > old_thresholds["large_positive_amount_cents"]).astype(int)
                + (X_val["count_1h"].to_numpy() > old_thresholds["high_prior_count_1h"]).astype(int)
                + ((X_val["has_history_30d"].to_numpy() == 1) & (X_val["merchant_seen_30d"].to_numpy() == 0)).astype(int)),
        }
        for name, scores in baseline_scores.items():
            metrics, queue = evaluate_scores(meta_val, y_val, scores, daily_budget=policy["daily_review_budget"], tie_seed=policy["tie_seed"])
            old = previous["strategies"][name]["daily_review"]
            if any(metrics["daily_review"][key] != old[key] for key in ("rows", "positives", "reviewed_or_alerted", "true_positives")):
                raise ValueError("Recomputed baseline ranking disagrees with frozen result")
            reports[name], queues[name], score_arrays[name] = metrics, queue, np.asarray(scores, dtype=np.float64)
        with threadpool_limits(limits=policy["threads"]):
            for name in policy["models"]:
                model = make_model(name, policy)
                print(f"Training {name}...", flush=True)
                model_start = time.monotonic()
                with warnings.catch_warnings(record=True) as captured:
                    warnings.simplefilter("always")
                    model.fit(X_train, y_train)
                fit_seconds = time.monotonic() - model_start
                train_scores = model.predict_proba(X_train)[:, 1]
                train_metrics = {"average_precision": float(average_precision_score(y_train, train_scores)),
                    "weighted_log_loss": float(log_loss(y_train, train_scores, sample_weight=train_weights, labels=[0, 1]))}
                del train_scores
                scores = model.predict_proba(X_val)[:, 1]
                metrics, queue = evaluate_scores(meta_val, y_val, scores, daily_budget=policy["daily_review_budget"], tie_seed=policy["tie_seed"])
                model_path = output / f"{name}.joblib"
                joblib.dump(model, model_path, compress=3)
                convergence_warning = any(issubclass(item.category, ConvergenceWarning) for item in captured)
                metrics.update(fit_seconds=round(fit_seconds, 3), train_metrics=train_metrics,
                    iterations=np.asarray(model.named_steps["model"].n_iter_).tolist(),
                    convergence_warning=convergence_warning,
                    warnings=[str(item.message) for item in captured], model_sha256=file_sha256(model_path),
                    error_groups=error_groups(meta_val, X_val, y_val, queue, train_users=train_users))
                if name == "logistic_regression":
                    feature_names = model.named_steps["preprocess"].get_feature_names_out()
                    coefficients = model.named_steps["model"].coef_[0]
                    explanation = [{"feature": str(feature_names[i]), "coefficient": float(coefficients[i])}
                                   for i in np.argsort(np.abs(coefficients))[::-1][:15]]
                    (output / "logistic-coefficients.json").write_text(json.dumps({"top_absolute_coefficients": explanation,
                        "interpretation": "Coefficients in transformed/scaled space; correlations, not causal evidence."}, indent=2) + "\n", encoding="utf-8")
                reports[name], queues[name], score_arrays[name] = metrics, queue, scores
                print(f"{name}: AP={metrics['average_precision']:.6f}, caught={metrics['daily_review']['true_positives']}, fit={fit_seconds:.1f}s", flush=True)
                del model
        np.savez_compressed(output / "validation-scores.npz",
            transaction_id=meta_val["transaction_id"].to_numpy(dtype=str), user_id=meta_val["user_id"].to_numpy(dtype=str),
            event_time=meta_val["event_time"].to_numpy(dtype=str), **score_arrays)
        with (output / "daily-review.jsonl").open("x", encoding="utf-8", newline="\n") as handle:
            for name, queue in queues.items():
                for row in queue:
                    handle.write(json.dumps({"strategy": name, **row}) + "\n")
        report = {"status": "validation_models_evaluated", "created_at": datetime.now(timezone.utc).isoformat(),
            "data_kind": "synthetic", "final_test_read": False, "train_rows": len(y_train), "train_positives": int(y_train.sum()),
            "validation_rows": len(y_val), "validation_positives": int(y_val.sum()),
            "balanced_class_weights": {str(i): float(value) for i, value in enumerate(class_weights)},
            "strategies": reports, "elapsed_seconds": round(time.monotonic() - started, 3),
            "artifact_sha256": {name: file_sha256(output / name) for name in (
                "frozen-experiment.json", "validation-scores.npz", "daily-review.jsonl")},
            "limitations": ["Synthetic retrospective validation, not final-test or real-world proof.",
                "One predeclared configuration/seed per model, not a completed tuning/robustness study.",
                "Weighted classifier outputs are not established calibrated probabilities.",
                "Daily ranking is retrospective; subgroups inherit the global review budget.",
                "Small subgroup and correlated transaction counts limit conclusions.",
                "Only load locally generated, hash-verified joblib files; never untrusted pickle/joblib."]}
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"output_directory": str(output), "status": report["status"],
                          "elapsed_seconds": report["elapsed_seconds"], "validation_summary": {
                              name: {"AP": stats["average_precision"], "daily_review": stats["daily_review"]}
                              for name, stats in reports.items()}}, indent=2), flush=True)
    except Exception as exc:
        (output / "failed.json").write_text(json.dumps({"status": "failed", "error": str(exc),
                                                       "error_type": type(exc).__name__}, indent=2) + "\n", encoding="utf-8")
        raise


if __name__ == "__main__":
    main()
