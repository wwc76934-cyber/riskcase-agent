"""Predeclared time/feature/weight diagnostics entirely inside the old train partition."""

import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import sqlite3
import subprocess
import time
from uuid import uuid4
import warnings

import joblib
import numpy as np
import sklearn
from sklearn.exceptions import ConvergenceWarning
from sklearn.metrics import average_precision_score
from threadpoolctl import threadpool_limits

from app.cli.cache_archive import file_sha256
from app.risk.ml_data import load_split
from app.risk.ml_evaluation import evaluate_scores, error_groups
from app.risk.temporal_experiment import VARIANTS, fold_masks, make_variant


def main():
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--features-manifest", type=Path, default=root / "data/features/features-b9692149f1c1/manifest.json")
    args = parser.parse_args()
    policy = json.loads((root / "config/temporal-experiment-v1.json").read_text(encoding="utf-8"))
    base = json.loads((root / "config/ml-experiment-v1.json").read_text(encoding="utf-8"))
    if tuple(policy["variants"]) != VARIANTS or sklearn.__version__ != "1.7.2":
        raise ValueError("Use frozen variants and pinned ML environment")
    if any(policy[key] != base[key] for key in ("random_seed", "threads", "daily_review_budget", "tie_seed")):
        raise ValueError("Comparison seed/resource/budget mismatch")
    manifest = json.loads(args.features_manifest.read_text(encoding="utf-8"))
    if manifest.get("status") != "features_built" or manifest.get("labels_read") is not False:
        raise ValueError("Label-free completed feature snapshot required")
    cohort_path = Path(manifest["cohort_report"])
    if file_sha256(cohort_path) != manifest["cohort_report_sha256"]:
        raise ValueError("Cohort report changed")
    cohort = json.loads(cohort_path.read_text(encoding="utf-8"))
    features = args.features_manifest.parent / "features.sqlite"
    labels = cohort_path.parent / "dataset/evaluation-labels.sqlite"
    if file_sha256(features) != manifest["features_sha256"] or file_sha256(labels) != cohort["artifact_sha256"]["dataset/evaluation-labels.sqlite"]:
        raise ValueError("Dataset changed")
    output = root / "runs" / f"temporal-v1-{uuid4().hex[:12]}"
    output.mkdir(parents=True, exist_ok=False)
    code_paths = ("app/cli/train_temporal.py", "app/risk/temporal_experiment.py", "app/risk/ml_models.py",
                  "app/risk/ml_evaluation.py", "app/risk/ml_data.py", "app/risk/rule_baseline.py")
    frozen = {"policy": policy, "base_model_policy": base,
        "features_manifest": str(args.features_manifest.resolve()),
        "features_manifest_sha256": file_sha256(args.features_manifest), "features_sha256": manifest["features_sha256"],
        "labels_sha256": cohort["artifact_sha256"]["dataset/evaluation-labels.sqlite"],
        "code_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
        "code_file_sha256": {name: file_sha256(root / name) for name in code_paths},
        "requirements_sha256": file_sha256(root / "requirements-ml.txt"),
        "python": platform.python_version(), "platform": platform.platform(),
        "sklearn": sklearn.__version__, "numpy": np.__version__,
        "created_at": datetime.now(timezone.utc).isoformat()}
    (output / "frozen-experiment.json").write_text(json.dumps(frozen, indent=2) + "\n", encoding="utf-8")
    os.environ.setdefault("LOKY_MAX_CPU_COUNT", str(policy["threads"]))
    started = time.monotonic()
    print(f"Run: {output}", flush=True)
    try:
        with closing(sqlite3.connect(features.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            db.execute("ATTACH DATABASE ? AS evaluation", (labels.resolve().as_uri() + "?mode=ro",))
            expected = cohort["partitions"]["train"]
            X, y, metadata = load_split(db, "train", expected_rows=expected["rows"], expected_positives=expected["fraud_rows"])
        reports, artifact_names = {}, ["frozen-experiment.json"]
        with threadpool_limits(limits=policy["threads"]):
            for fold in policy["folds"]:
                fit_mask, evaluate_mask = fold_masks(metadata, fold)
                fit_X, eval_X = X.loc[fit_mask], X.loc[evaluate_mask]
                fit_y, eval_y = y[fit_mask], y[evaluate_mask]
                fit_meta, eval_meta = metadata.loc[fit_mask], metadata.loc[evaluate_mask]
                if len(np.unique(fit_y)) != 2:
                    raise ValueError("Training window needs both classes")
                print(f"{fold['name']}: fit={len(fit_y)}/{int(fit_y.sum())} positive; evaluate={len(eval_y)}/{int(eval_y.sum())} positive", flush=True)
                fold_report = {"fit_rows": len(fit_y), "fit_positives": int(fit_y.sum()),
                    "evaluate_rows": len(eval_y), "evaluate_positives": int(eval_y.sum()),
                    "AP_defined": bool(eval_y.sum()), "variants": {}}
                arrays = {"transaction_id": eval_meta["transaction_id"].to_numpy(dtype=str),
                    "user_id": eval_meta["user_id"].to_numpy(dtype=str),
                    "event_time": eval_meta["event_time"].to_numpy(dtype=str)}
                queues = []
                for name in policy["variants"]:
                    model = make_variant(name, base, policy)
                    begin = time.monotonic()
                    with warnings.catch_warnings(record=True) as captured:
                        warnings.simplefilter("always")
                        model.fit(fit_X, fit_y)
                    fit_seconds = time.monotonic() - begin
                    train_scores = model.predict_proba(fit_X)[:, 1]
                    train_ap = float(average_precision_score(fit_y, train_scores))
                    del train_scores
                    scores = model.predict_proba(eval_X)[:, 1]
                    stats, queue = evaluate_scores(eval_meta, eval_y, scores, expected_split="train",
                        daily_budget=policy["daily_review_budget"], tie_seed=policy["tie_seed"])
                    stats.update(train_AP=train_ap, fit_seconds=round(fit_seconds, 3),
                        iterations=np.asarray(model.named_steps["model"].n_iter_).tolist(),
                        convergence_warning=any(issubclass(item.category, ConvergenceWarning) for item in captured),
                        warnings=[str(item.message) for item in captured],
                        error_groups=error_groups(eval_meta, eval_X, eval_y, queue, train_users=set(fit_meta["user_id"])))
                    per_month = {}
                    queue_ids = {row["transaction_id"] for row in queue}
                    months = eval_meta["event_time"].str[:7]
                    selected = eval_meta["transaction_id"].isin(queue_ids).to_numpy()
                    for month in sorted(months.unique()):
                        mask = months.eq(month).to_numpy()
                        positives, caught = int(eval_y[mask].sum()), int(eval_y[mask & selected].sum())
                        per_month[month] = {"rows": int(mask.sum()), "positives": positives,
                            "reviewed": int(selected[mask].sum()), "caught": caught,
                            "recall": caught / positives if positives else None}
                    stats["by_month"] = per_month
                    filename = f"{fold['name']}-{name}.joblib"
                    joblib.dump(model, output / filename, compress=3)
                    stats["model_file"] = filename
                    artifact_names.append(filename)
                    # Reload each locally created model and check sampled predictions.
                    positions = np.random.default_rng(policy["random_seed"]).choice(len(eval_X), size=64, replace=False)
                    loaded = joblib.load(output / filename)
                    difference = float(np.max(np.abs(loaded.predict_proba(eval_X.iloc[positions])[:, 1] - scores[positions])))
                    if difference > 1e-12:
                        raise ValueError("Reloaded model predictions changed")
                    stats["reload_check"] = {"rows": 64, "max_absolute_difference": difference}
                    fold_report["variants"][name], arrays[name] = stats, scores
                    queues.extend({"variant": name, **row} for row in queue)
                    print(f"  {name}: AP={stats['average_precision']}, caught={stats['daily_review']['true_positives']}, fit={fit_seconds:.1f}s", flush=True)
                    del model, loaded
                scores_name, queue_name = f"{fold['name']}-scores.npz", f"{fold['name']}-review.jsonl"
                np.savez_compressed(output / scores_name, **arrays)
                with (output / queue_name).open("x", encoding="utf-8", newline="\n") as handle:
                    for row in queues:
                        handle.write(json.dumps(row) + "\n")
                artifact_names.extend([scores_name, queue_name])
                reports[fold["name"]] = fold_report
        report = {"status": "temporal_diagnostics_completed", "data_kind": "synthetic",
            "read_partitions": ["train"], "validation_2018_evaluated": False, "final_test_evaluated": False,
            "folds": reports, "elapsed_seconds": round(time.monotonic()-started, 3),
            "artifact_sha256": {name: file_sha256(output / name) for name in artifact_names},
            "limitations": policy["limitations"] + ["One seed and two windows, not population or causal proof.",
                "No automatic best-model selection; one evaluation window has no positive labels.",
                "Only load trusted locally generated joblib artifacts."]}
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"output_directory": str(output), "status": report["status"], "elapsed_seconds": report["elapsed_seconds"]}), flush=True)
    except Exception as exc:
        (output / "failed.json").write_text(json.dumps({"status": "failed", "error": str(exc), "type": type(exc).__name__}) + "\n", encoding="utf-8")
        raise


if __name__ == "__main__":
    main()
