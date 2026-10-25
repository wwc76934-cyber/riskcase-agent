"""Select MLP epochs on a historical window, refit, then evaluate 2018 once."""

import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import sqlite3
import subprocess
import time
from uuid import uuid4

import joblib
import numpy as np
import sklearn
from sklearn.metrics import average_precision_score
import torch
from threadpoolctl import threadpool_limits

from app.cli.cache_archive import file_sha256
from app.risk.ml_data import load_split
from app.risk.ml_evaluation import evaluate_scores, error_groups
from app.risk.ml_models import MODEL_COLUMNS, make_model
from app.risk.neural_model import RiskMLP, EarlyStop, dense_float32, make_loader, make_loss, predict_batches, seed_training, train_one_epoch
from app.risk.temporal_experiment import fold_masks


def main():
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--features-manifest", type=Path, default=root / "data/features/features-b9692149f1c1/manifest.json")
    parser.add_argument("--baseline-report", type=Path, default=root / "runs/ml-v1-90f95014b365/report.json")
    args = parser.parse_args()
    policy = json.loads((root / "config/mlp-experiment-v1.json").read_text(encoding="utf-8"))
    base = json.loads((root / "config/ml-experiment-v1.json").read_text(encoding="utf-8"))
    if torch.__version__ != "2.8.0+cpu" or sklearn.__version__ != "1.7.2":
        raise ValueError("Use the pinned CPU training environment")
    if policy["device"] != "cpu" or policy["hidden_widths"] != [64, 32] or policy["max_epochs"] != 20:
        raise ValueError("Use the predeclared first neural experiment")
    if any(policy[key] != base[key] for key in ("random_seed", "threads", "daily_review_budget", "tie_seed")):
        raise ValueError("Comparison budget/resources mismatch")
    manifest = json.loads(args.features_manifest.read_text(encoding="utf-8"))
    if manifest.get("status") != "features_built" or manifest.get("labels_read") is not False:
        raise ValueError("Completed label-free features required")
    cohort_path = Path(manifest["cohort_report"])
    if file_sha256(cohort_path) != manifest["cohort_report_sha256"]:
        raise ValueError("Cohort changed")
    cohort = json.loads(cohort_path.read_text(encoding="utf-8"))
    feature_path = args.features_manifest.parent / "features.sqlite"
    labels_path = cohort_path.parent / "dataset/evaluation-labels.sqlite"
    if file_sha256(feature_path) != manifest["features_sha256"] or file_sha256(labels_path) != cohort["artifact_sha256"]["dataset/evaluation-labels.sqlite"]:
        raise ValueError("Dataset changed")
    baseline = json.loads(args.baseline_report.read_text(encoding="utf-8"))
    old_frozen_path = args.baseline_report.parent / "frozen-experiment.json"
    if file_sha256(old_frozen_path) != baseline["artifact_sha256"]["frozen-experiment.json"]:
        raise ValueError("Baseline protocol changed")
    old_frozen = json.loads(old_frozen_path.read_text(encoding="utf-8"))
    if old_frozen["features_sha256"] != manifest["features_sha256"] or any(
            policy[key] != old_frozen["policy"][key] for key in ("daily_review_budget", "tie_seed")):
        raise ValueError("Baseline data or budget mismatch")
    provenance = root / "config/pytorch-installation.json"
    if not provenance.is_file():
        raise ValueError("Record installer provenance before training")
    output = root / "runs" / f"mlp-v1-{uuid4().hex[:12]}"
    output.mkdir(parents=True, exist_ok=False)
    paths = ("app/cli/train_mlp.py", "app/risk/neural_model.py", "app/risk/ml_data.py", "app/risk/ml_models.py",
             "app/risk/ml_evaluation.py", "app/risk/temporal_experiment.py", "app/risk/rule_baseline.py")
    frozen = {"policy": policy, "preprocessing_policy": base, "raw_model_columns": MODEL_COLUMNS,
        "features_manifest": str(args.features_manifest.resolve()), "features_manifest_sha256": file_sha256(args.features_manifest),
        "features_sha256": manifest["features_sha256"], "labels_sha256": file_sha256(labels_path),
        "baseline_report": str(args.baseline_report.resolve()), "baseline_report_sha256": file_sha256(args.baseline_report),
        "code_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
        "code_file_sha256": {name: file_sha256(root / name) for name in paths},
        "requirements_sha256": file_sha256(root / "requirements-dl.txt"),
        "installer_provenance_sha256": file_sha256(provenance),
        "versions": {"python": platform.python_version(), "torch": torch.__version__, "sklearn": sklearn.__version__, "numpy": np.__version__},
        "platform": platform.platform(), "frozen_at": datetime.now(timezone.utc).isoformat()}
    (output / "frozen-experiment.json").write_text(json.dumps(frozen, indent=2) + "\n", encoding="utf-8")
    started = time.monotonic()
    print(f"Run: {output}", flush=True)
    artifacts = ["frozen-experiment.json"]
    try:
        with closing(sqlite3.connect(feature_path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            db.execute("ATTACH DATABASE ? AS evaluation", (labels_path.resolve().as_uri() + "?mode=ro",))
            expected = cohort["partitions"]["train"]
            X, y, metadata = load_split(db, "train", expected_rows=expected["rows"], expected_positives=expected["fraud_rows"])
        fold = {"fit_start": policy["selection_fit"]["start"], "fit_end": policy["selection_fit"]["end"],
                "evaluate_start": policy["selection_validation"]["start"], "evaluate_end": policy["selection_validation"]["end"]}
        fit_mask, eval_mask = fold_masks(metadata, fold)
        if int(y[eval_mask].sum()) != policy["selection_validation"]["known_positives"]:
            raise ValueError("Selection-window positives changed")
        def preprocess_fit(frame):
            prep = make_model("logistic_regression", base).named_steps["preprocess"]
            return prep, dense_float32(prep.fit_transform(frame), max_bytes=policy["max_dense_bytes"])
        def build(width):
            return RiskMLP(width, policy["hidden_widths"], policy["dropout"])
        def loader(features, targets=None, shuffle=False):
            return make_loader(features, targets, batch_size=policy["batch_size"], shuffle=shuffle, seed=policy["random_seed"])
        def optimizer(model):
            return torch.optim.AdamW(model.parameters(), lr=policy["learning_rate"], weight_decay=policy["weight_decay"])
        curves = []
        with threadpool_limits(limits=policy["threads"]):
            seed_training(policy["random_seed"], policy["threads"])
            prep, fit_features = preprocess_fit(X.loc[fit_mask])
            eval_features = dense_float32(prep.transform(X.loc[eval_mask]), max_bytes=policy["max_dense_bytes"])
            train_loader, train_eval_loader = loader(fit_features, y[fit_mask], True), loader(fit_features, y[fit_mask])
            eval_loader = loader(eval_features, y[eval_mask])
            model = build(fit_features.shape[1])
            criterion, selection_weight = make_loss(y[fit_mask])
            optim = optimizer(model)
            stop = EarlyStop(patience=policy["early_stopping"]["patience"], minimum_improvement=policy["early_stopping"]["minimum_improvement"])
            print(f"Selection: fit={fit_features.shape}, evaluate={eval_features.shape}, pos_weight={selection_weight:.3f}", flush=True)
            for epoch in range(1, policy["max_epochs"]+1):
                begin = time.monotonic()
                training = train_one_epoch(model, train_loader, optim, criterion)
                train_scores, train_loss = predict_batches(model, train_eval_loader, criterion)
                eval_scores, eval_loss = predict_batches(model, eval_loader, criterion)
                ap = float(average_precision_score(y[eval_mask], eval_scores))
                improved, should_stop = stop.update(ap, epoch, model)
                row = {"phase": "selection", "epoch": epoch, "training_updates": training,
                    "train_eval_weighted_loss": train_loss, "train_AP": float(average_precision_score(y[fit_mask], train_scores)),
                    "selection_weighted_loss": eval_loss, "selection_AP": ap, "new_best": improved,
                    "elapsed_seconds": round(time.monotonic()-begin, 3)}
                curves.append(row)
                with (output / "curves.jsonl").open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(row) + "\n")
                if improved:
                    torch.save(stop.best_state, output / "selection-best.pt")
                print(f"selection epoch {epoch}: train_loss={train_loss:.5f}, selection_loss={eval_loss:.5f}, AP={ap:.6f}, best={improved}", flush=True)
                if should_stop:
                    break
            model.load_state_dict(torch.load(output / "selection-best.pt", map_location="cpu", weights_only=True))
            selection_scores, _ = predict_batches(model, eval_loader)
            selection_metrics, _ = evaluate_scores(metadata.loc[eval_mask], y[eval_mask], selection_scores, expected_split="train",
                daily_budget=policy["daily_review_budget"], tie_seed=policy["tie_seed"])
            selection_details = {"input_width": fit_features.shape[1], "fit_rows": int(fit_mask.sum()),
                "fit_positives": int(y[fit_mask].sum()), "validation_rows": int(eval_mask.sum()),
                "validation_positives": int(y[eval_mask].sum()), "pos_weight": selection_weight,
                "epochs_run": epoch, "selected_epoch": stop.best_epoch, "metrics": selection_metrics}
            joblib.dump(prep, output / "selection-preprocess.joblib", compress=3)
            del model, optim, train_loader, train_eval_loader, eval_loader, fit_features, eval_features, prep, train_scores, eval_scores
            # Refit from scratch on all original train records, for the chosen count.
            seed_training(policy["random_seed"], policy["threads"])
            prep, full_features = preprocess_fit(X)
            model, criterion = build(full_features.shape[1]), None
            criterion, refit_weight = make_loss(y)
            optim, train_loader = optimizer(model), loader(full_features, y, True)
            print(f"Refit: {full_features.shape}, epochs={stop.best_epoch}, pos_weight={refit_weight:.3f}", flush=True)
            for epoch in range(1, stop.best_epoch+1):
                begin = time.monotonic()
                training = train_one_epoch(model, train_loader, optim, criterion)
                row = {"phase": "refit", "epoch": epoch, "training_updates": training,
                    "elapsed_seconds": round(time.monotonic()-begin, 3)}
                curves.append(row)
                with (output / "curves.jsonl").open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(row) + "\n")
                print(f"refit epoch {epoch}: update_loss={training['weighted_loss']:.5f}", flush=True)
            train_scores, final_train_loss = predict_batches(model, loader(full_features, y), criterion)
            final_train_ap = float(average_precision_score(y, train_scores))
            del train_loader, full_features, train_scores, optim
            torch.save(model.state_dict(), output / "refit-model.pt")
            joblib.dump(prep, output / "refit-preprocess.joblib", compress=3)
            # No 2018 query before epoch selection and refit are complete.
            with closing(sqlite3.connect(feature_path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
                db.execute("ATTACH DATABASE ? AS evaluation", (labels_path.resolve().as_uri() + "?mode=ro",))
                expected = cohort["partitions"]["validation"]
                val_X, val_y, val_meta = load_split(db, "validation", expected_rows=expected["rows"], expected_positives=expected["fraud_rows"])
            val_features = dense_float32(prep.transform(val_X), max_bytes=policy["max_dense_bytes"])
            scores, val_loss = predict_batches(model, loader(val_features, val_y), criterion)
            metrics, queue = evaluate_scores(val_meta, val_y, scores, daily_budget=policy["daily_review_budget"], tie_seed=policy["tie_seed"])
            metrics["error_groups"] = error_groups(val_meta, val_X, val_y, queue, train_users=set(metadata["user_id"]))
            loaded = build(val_features.shape[1])
            loaded.load_state_dict(torch.load(output / "refit-model.pt", map_location="cpu", weights_only=True))
            positions = np.random.default_rng(42).choice(len(scores), 128, replace=False)
            reloaded, _ = predict_batches(loaded, loader(val_features[positions]))
            difference = float(np.max(np.abs(reloaded-scores[positions])))
            if difference > 1e-6:
                raise ValueError("Reloaded network predictions differ beyond float32 tolerance")
            architecture = {"refit_input_width": val_features.shape[1], "selection_input_width": selection_details["input_width"],
                "hidden_widths": policy["hidden_widths"], "dropout": policy["dropout"],
                "parameter_count": sum(value.numel() for value in model.parameters()),
                "feature_names": prep.get_feature_names_out().tolist(), "dtype": "float32"}
        (output / "architecture.json").write_text(json.dumps(architecture, indent=2) + "\n", encoding="utf-8")
        np.savez_compressed(output / "validation-scores.npz", transaction_id=val_meta["transaction_id"].to_numpy(dtype=str),
            user_id=val_meta["user_id"].to_numpy(dtype=str), event_time=val_meta["event_time"].to_numpy(dtype=str), mlp=scores)
        with (output / "daily-review.jsonl").open("x", encoding="utf-8", newline="\n") as handle:
            for row in queue:
                handle.write(json.dumps(row) + "\n")
        artifacts += ["curves.jsonl", "selection-best.pt", "selection-preprocess.joblib", "refit-model.pt",
            "refit-preprocess.joblib", "architecture.json", "validation-scores.npz", "daily-review.jsonl"]
        comparisons = {name: {"AP": stats["average_precision"], "caught": stats["daily_review"]["true_positives"],
            "reviewed": stats["daily_review"]["reviewed_or_alerted"]} for name, stats in baseline["strategies"].items()}
        report = {"status": "mlp_development_evaluated", "data_kind": "synthetic", "final_test_evaluated": False,
            "selection": selection_details, "refit": {"rows": len(y), "positives": int(y.sum()), "epochs": stop.best_epoch,
                "pos_weight": refit_weight, "train_AP": final_train_ap, "train_eval_weighted_loss": final_train_loss},
            "validation_rows": len(val_y), "validation_positives": int(val_y.sum()), "validation_weighted_loss": val_loss,
            "metrics": metrics, "baseline_comparison": comparisons,
            "reload_check": {"rows": 128, "max_absolute_difference": difference, "tolerance": 1e-6},
            "elapsed_seconds": round(time.monotonic()-started, 3),
            "artifact_sha256": {name: file_sha256(output / name) for name in artifacts},
            "limitations": policy["limitations"] + ["Weighted outputs are not calibrated real-world probabilities.",
                "2018 is a previously observed development set; no final or real-world effectiveness claim.",
                "Training-update loss uses dropout and changing parameters; eval loss uses fixed parameters without dropout."]}
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"output_directory": str(output), "selected_epoch": stop.best_epoch,
            "validation_AP": metrics["average_precision"], "daily_review": metrics["daily_review"],
            "elapsed_seconds": report["elapsed_seconds"]}), flush=True)
    except (Exception, KeyboardInterrupt) as exc:
        (output / "failed.json").write_text(json.dumps({"error_type": type(exc).__name__, "error": str(exc)}) + "\n", encoding="utf-8")
        raise


if __name__ == "__main__":
    main()
