"""Bounded developer search and three-seed confirmation; never queries final 2019 rows."""

import argparse
from contextlib import closing
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import platform
import sqlite3
import subprocess
import time
from uuid import uuid4

import numpy as np
from sklearn.metrics import average_precision_score, roc_auc_score
from threadpoolctl import threadpool_limits
import torch

from app.risk.history import sha256_file
from app.risk.ml_data import load_split
from app.risk.ml_evaluation import evaluate_scores, error_groups
from app.risk.neural_v2 import NeuralConfig, fit_v2, load_v2
from app.risk.source_merchant_pack import write_json


def load_development(root):
    manifest_path = root / "data/features/features-b9692149f1c1/manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    cohort_path = Path(manifest["cohort_report"])
    cohort = json.loads(cohort_path.read_text(encoding="utf-8"))
    features = manifest_path.parent / "features.sqlite"
    labels = cohort_path.parent / "dataset/evaluation-labels.sqlite"
    if sha256_file(cohort_path) != manifest["cohort_report_sha256"] or sha256_file(features) != manifest["features_sha256"] or sha256_file(labels) != cohort["artifact_sha256"]["dataset/evaluation-labels.sqlite"]: raise ValueError("Data provenance changed")
    with closing(sqlite3.connect(features.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("ATTACH DATABASE ? AS evaluation", (labels.resolve().as_uri() + "?mode=ro",))
        loaded = [load_split(db, split, expected_rows=cohort["partitions"][split]["rows"], expected_positives=cohort["partitions"][split]["fraud_rows"]) for split in ("train", "validation")]
    if any(not meta["event_time"].lt("2019-01-01").all() for _, _, meta in loaded): raise ValueError("Final rows forbidden")
    return loaded, {"manifest": str(manifest_path), "manifest_sha256": sha256_file(manifest_path), "features_sha256": manifest["features_sha256"], "labels_sha256": sha256_file(labels)}


class DevelopmentMetric:
    """Vectorized per-day ranking; cross-checked against independent DailyReviewer."""
    def __init__(self, metadata, y, budget=10, tie_seed="riskcase-review-ties-v1"):
        self.y, self.budget = np.asarray(y), budget
        days = metadata["event_time"].str[:10].to_numpy()
        self.ids = metadata["transaction_id"].to_numpy(dtype=str)
        self.ties = np.asarray([hashlib.sha256(f"{tie_seed}\0{i}".encode()).hexdigest() for i in self.ids])
        self.groups = [np.flatnonzero(days == day) for day in sorted(set(days))]

    def calculate(self, scores):
        scores = np.asarray(scores)
        if len(scores) != len(self.y) or not np.isfinite(scores).all(): raise ValueError("Invalid scores")
        selected = np.concatenate([group[np.lexsort((self.ids[group], self.ties[group], scores[group]))[::-1][:self.budget]] for group in self.groups])
        return {"average_precision": float(average_precision_score(self.y, scores)),
                "true_positives": int(self.y[selected].sum()), "reviewed": len(selected), "positives": int(self.y.sum()),
                "precision": float(self.y[selected].sum() / len(selected)), "recall": float(self.y[selected].sum() / self.y.sum())}


def train(root, directory):
    directory.mkdir(parents=True, exist_ok=False)
    policy = json.loads((root / "config/neural-experiment-v2.json").read_text(encoding="utf-8"))
    code = ["app/risk/neural_v2.py", "app/cli/train_neural_v2.py", "app/risk/ml_data.py", "app/risk/ml_evaluation.py", "app/risk/neural_model.py", "app/risk/rule_baseline.py"]
    write_json(directory / "protocol.json", {"policy": policy, "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
               "code_sha256": {p: sha256_file(root / p) for p in code}, "python": platform.python_version(), "torch": torch.__version__, "final_test_read": False})
    started = time.monotonic()
    (X, y, meta), (VX, vy, vmeta) = (loaded := load_development(root))[0]
    write_json(directory / "data.json", loaded[1])
    metric = DevelopmentMetric(vmeta, vy)
    results = []
    for recipe in policy["recipes"]:
        name = recipe["id"]
        config = NeuralConfig(**(policy["base_config"] | {k: v for k, v in recipe.items() if k != "id"}))
        output = directory / name; output.mkdir()
        rows, best, best_state, best_scores = [], None, None, None
        print(f"Training {name}: {asdict(config)}", flush=True)
        begin = time.monotonic()
        def observe(row, predictor, scores):
            nonlocal best, best_state, best_scores
            record = row | metric.calculate(scores) | {"elapsed_seconds": round(time.monotonic() - begin, 3)}
            rows.append(record)
            if best is None or (record["true_positives"], record["average_precision"], -record["epoch"]) > (best["true_positives"], best["average_precision"], -best["epoch"]):
                best, best_state, best_scores = record, deepcopy(predictor.model.state_dict()), scores.copy()
            with (output / "curves.jsonl").open("a", encoding="utf-8") as handle: handle.write(json.dumps(record) + "\n")
            print(f"{name} epoch {record['epoch']}: TP10={record['true_positives']} AP={record['average_precision']:.5f} loss={record['loss']:.5f}", flush=True)
        with threadpool_limits(limits=config.threads):
            predictor = fit_v2(X, y, config, seed=policy["seed"], epochs=policy["max_epochs"], validation=VX, on_epoch=observe)
        predictor.model.load_state_dict(best_state); predictor.save(output / "model")
        np.save(output / "development-scores.npy", best_scores)
        # Slow independent ranking once per model, not every training epoch.
        checked, queue = evaluate_scores(vmeta, vy, best_scores)
        if checked["daily_review"]["true_positives"] != best["true_positives"] or abs(checked["average_precision"] - best["average_precision"]) > 1e-12: raise ValueError("Ranking verification differs")
        checked["error_groups"] = error_groups(vmeta, VX, vy, queue, train_users=set(meta["user_id"]))
        positions = np.random.default_rng(42).choice(len(VX), 128, replace=False)
        reload_scores = load_v2(output / "model").predict(VX.iloc[positions])
        difference = float(np.max(np.abs(reload_scores - best_scores[positions])))
        if difference > 1e-6: raise ValueError("Reload predictions differ")
        result = {"recipe": name, "config": asdict(config), "best": best, "metrics": checked, "reload_max_abs_diff": difference, "epochs_attempted": len(rows), "parameter_count": sum(p.numel() for p in predictor.model.parameters()), "elapsed_seconds": round(time.monotonic() - begin, 3)}
        write_json(output / "result.json", result); results.append(result)
    chosen = max(results, key=lambda r: (r["best"]["true_positives"], r["best"]["average_precision"], -r["best"]["epoch"], -next(i for i, t in enumerate(policy["recipes"]) if t["id"] == r["recipe"])))
    freeze = {"recipe": chosen["recipe"], "config": chosen["config"], "epochs": chosen["best"]["epoch"], "seeds": policy["confirmation_seeds"],
              "final_test_read": False, "training_period": "2016-2017 for development comparison; final refit may include 2018 before one-time 2019 test",
              "selection_protocol_sha256": sha256_file(directory / "protocol.json")}
    write_json(directory / "selected-recipe.json", freeze)
    print(f"Selected {freeze['recipe']} epoch {freeze['epochs']}; confirming three seeds", flush=True)
    seeded, scores = [], []
    for seed in freeze["seeds"]:
        output = directory / f"confirmation-seed-{seed}"; output.mkdir()
        begin = time.monotonic()
        curves = []
        with threadpool_limits(limits=freeze["config"]["threads"]):
            predictor = fit_v2(X, y, freeze["config"], seed=seed, epochs=freeze["epochs"], on_epoch=lambda row, *_: curves.append(row))
        score = predictor.predict(VX); predictor.save(output / "model")
        np.save(output / "development-scores.npy", score)
        write_json(output / "curves.json", curves)
        metrics, queue = evaluate_scores(vmeta, vy, score)
        metrics["error_groups"] = error_groups(vmeta, VX, vy, queue, train_users=set(meta["user_id"]))
        seeded.append({"seed": seed, "metrics": metrics, "elapsed_seconds": round(time.monotonic() - begin, 3)})
        scores.append(score)
        write_json(output / "result.json", seeded[-1])
        print(f"seed {seed}: TP10={metrics['daily_review']['true_positives']} AP={metrics['average_precision']:.5f}", flush=True)
    ensemble = np.mean(scores, axis=0)
    np.save(directory / "ensemble-development-scores.npy", ensemble)
    metrics, queue = evaluate_scores(vmeta, vy, ensemble)
    metrics["error_groups"] = error_groups(vmeta, VX, vy, queue, train_users=set(meta["user_id"]))
    report = {"status": "neural_v2_development_complete", "data_kind": "synthetic", "final_test_read": False,
              "fit_rows": len(y), "fit_positives": int(y.sum()), "development_rows": len(vy), "development_positives": int(vy.sum()),
              "recipes": results, "selected": freeze, "seeds": seeded, "ensemble": metrics,
              "elapsed_seconds": round(time.monotonic() - started, 3), "limits": policy["limits"],
              "artifact_sha256": {str(p.relative_to(directory)).replace("\\", "/"): sha256_file(p) for p in directory.rglob("*") if p.is_file()}}
    write_json(directory / "report.json", report)
    print(json.dumps({"directory": str(directory), "selected": freeze, "ensemble": metrics, "seconds": report["elapsed_seconds"]}), flush=True)
    return report


def main():
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=root / "runs" / ("neural-v2-" + uuid4().hex[:12]))
    args = parser.parse_args()
    train(root, args.output)


if __name__ == "__main__": main()
