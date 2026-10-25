"""Recompute all score metrics/checkpoint selections without querying final-year data."""

import argparse
import json
from pathlib import Path

import numpy as np
import joblib
from threadpoolctl import threadpool_limits
import torch

from app.cli.train_neural_v2 import DevelopmentMetric, load_development
from app.risk.history import sha256_file
from app.risk.neural_v2 import predict_v2
from app.risk.source_merchant_pack import write_json


def independent_ap(y, score):
    order = np.argsort(-np.asarray(score), kind="mergesort")
    sorted_scores, outcomes = np.asarray(score)[order], np.asarray(y)[order]
    ends = np.r_[np.flatnonzero(sorted_scores[1:] != sorted_scores[:-1]), len(score) - 1]
    cumulative = outcomes.cumsum()[ends]
    return float(np.sum(np.diff(np.r_[0, cumulative]) * cumulative / (ends + 1)) / outcomes.sum())


def verify(root, directory, matched_tree=None):
    torch.set_num_threads(4)
    report = json.loads((directory / "report.json").read_text(encoding="utf-8"))
    protocol = json.loads((directory / "protocol.json").read_text(encoding="utf-8"))
    if report["final_test_read"] or protocol["final_test_read"]: raise ValueError("Unexpected final access")
    for name, expected in report["artifact_sha256"].items():
        path = (directory / name).resolve()
        if not path.is_relative_to(directory.resolve()) or sha256_file(path) != expected: raise ValueError("Run artifact changed")
    for name, expected in protocol["code_sha256"].items():
        if sha256_file(root / name) != expected: raise ValueError("Experiment code changed")
    loaded, data = load_development(root)
    if data != json.loads((directory / "data.json").read_text(encoding="utf-8")): raise ValueError("Source differs")
    _, _, metadata = loaded[0]; X, y, meta = loaded[1]
    metric = DevelopmentMetric(meta, y)
    checks = []
    for recipe in report["recipes"]:
        output = directory / recipe["recipe"]
        curves = [json.loads(line) for line in (output / "curves.jsonl").read_text(encoding="utf-8").splitlines()]
        if len(curves) != protocol["policy"]["max_epochs"] or [r["epoch"] for r in curves] != list(range(1, len(curves) + 1)): raise ValueError("Epoch record incomplete")
        best = max(curves, key=lambda r: (r["true_positives"], r["average_precision"], -r["epoch"]))
        if best != recipe["best"] or any(r["rows"] != len(metadata) or r["batches"] != (len(metadata) + recipe["config"]["batch_size"] - 1) // recipe["config"]["batch_size"] for r in curves): raise ValueError("Checkpoint/row selection differs")
        score = np.load(output / "development-scores.npy", allow_pickle=False)
        actual = metric.calculate(score)
        if actual["true_positives"] != best["true_positives"] or abs(independent_ap(y, score) - best["average_precision"]) > 1e-12: raise ValueError("Score metrics differ")
        positions = np.unique(np.r_[np.random.default_rng(42).choice(len(y), 128, replace=False), np.argsort(score)[-16:]])
        reloaded = predict_v2(output / "model", X.iloc[positions])
        difference = float(np.max(np.abs(reloaded - score[positions])))
        if difference > 1e-6: raise ValueError("Model reload differs")
        checks.append({"recipe": recipe["recipe"], "rows": len(y), "independent_AP": independent_ap(y, score), "TP10": actual["true_positives"], "reload_rows": len(positions), "reload_max_diff": difference})
    order = {recipe["id"]: i for i, recipe in enumerate(protocol["policy"]["recipes"])}
    selected = max(report["recipes"], key=lambda r: (r["best"]["true_positives"], r["best"]["average_precision"], -r["best"]["epoch"], -order[r["recipe"]]))
    if report["selected"]["recipe"] != selected["recipe"] or report["selected"]["epochs"] != selected["best"]["epoch"]: raise ValueError("Selected recipe differs")
    seeds = report["selected"]["seeds"]
    if seeds != [42, 43, 44] or [r["seed"] for r in report["seeds"]] != seeds: raise ValueError("Seed results incomplete")
    scores = []
    for item in report["seeds"]:
        output = directory / f"confirmation-seed-{item['seed']}"
        curves = json.loads((output / "curves.json").read_text(encoding="utf-8"))
        if len(curves) != report["selected"]["epochs"]: raise ValueError("Confirmation epochs differ")
        score = np.load(output / "development-scores.npy", allow_pickle=False)
        actual = metric.calculate(score)
        if actual["true_positives"] != item["metrics"]["daily_review"]["true_positives"] or abs(independent_ap(y, score) - item["metrics"]["average_precision"]) > 1e-12: raise ValueError("Seed metrics differ")
        scores.append(score)
    ensemble = np.load(directory / "ensemble-development-scores.npy", allow_pickle=False)
    if not np.array_equal(ensemble, np.mean(scores, axis=0)): raise ValueError("Ensemble differs")
    actual = metric.calculate(ensemble)
    if actual["true_positives"] != report["ensemble"]["daily_review"]["true_positives"] or abs(independent_ap(y, ensemble) - report["ensemble"]["average_precision"]) > 1e-12: raise ValueError("Ensemble metrics differ")
    proof = {"status": "verified", "report_sha256": sha256_file(directory / "report.json"), "recipes": checks,
             "seeds": seeds, "ensemble_TP10": actual["true_positives"], "ensemble_AP": independent_ap(y, ensemble),
             "seed_TP10_mean": float(np.mean([r["metrics"]["daily_review"]["true_positives"] for r in report["seeds"]])),
             "seed_TP10_sample_std": float(np.std([r["metrics"]["daily_review"]["true_positives"] for r in report["seeds"]], ddof=1)),
             "final_test_read": False, "limitations": "Development selection, not independent final generalization."}
    if matched_tree is not None:
        tree = json.loads((matched_tree / "report.json").read_text(encoding="utf-8"))
        if tree["data"] != data or tree["final_test_read"]: raise ValueError("Tree comparison data differ")
        for name, expected in tree["artifact_sha256"].items():
            if sha256_file(matched_tree / name) != expected: raise ValueError("Tree control artifact changed")
        score = np.load(matched_tree / "development-scores.npy", allow_pickle=False)
        actual = metric.calculate(score)
        if actual["true_positives"] != tree["metrics"]["daily_review"]["true_positives"] or abs(independent_ap(y, score) - tree["metrics"]["average_precision"]) > 1e-12: raise ValueError("Tree control score metrics differ")
        positions = np.unique(np.r_[np.random.default_rng(42).choice(len(y), 128, replace=False), np.argsort(score)[-16:]])
        with threadpool_limits(limits=2):
            reloaded = joblib.load(matched_tree / "model.joblib").predict_proba(X.iloc[positions])[:, 1]
        difference = float(np.max(np.abs(reloaded - score[positions])))
        if difference > 1e-12: raise ValueError("Tree control model reload differs")
        proof["matched_tree"] = {"TP10": actual["true_positives"], "AP": independent_ap(y, score), "reload_max_diff": difference, "report_sha256": sha256_file(matched_tree / "report.json")}
    write_json(directory / "verification.json", proof)
    return proof


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--matched-tree", type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(root, args.directory, args.matched_tree), ensure_ascii=False, indent=2))


if __name__ == "__main__": main()
