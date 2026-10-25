"""Matched-input HGB control; no final-year records and no search over hyperparameters."""

import argparse
import json
from pathlib import Path
import time
from uuid import uuid4

import joblib
import numpy as np
from threadpoolctl import threadpool_limits

from app.cli.train_neural_v2 import load_development
from app.risk.detector_v2_controls import make_hgb_v2
from app.risk.history import sha256_file
from app.risk.ml_evaluation import evaluate_scores, error_groups
from app.risk.source_merchant_pack import write_json


def main():
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=root / "runs" / ("hgb-v2-" + uuid4().hex[:12]))
    parser.add_argument("--raw-features", action="store_true", help="No derived numeric features; MCC retained, channel omitted")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    write_json(args.output / "protocol.json", {"seed": 42, "threads": 2, "max_iter": 100, "leaves": 15,
               "fit": "2016-2017", "development": "2018", "final_test_read": False,
               "change_from_original": "Same hyperparameters; neural numeric view + MCC, no channel", "engineered": not args.raw_features,
               "code_sha256": {name: sha256_file(root / name) for name in ("app/risk/detector_v2_controls.py", "app/risk/neural_v2.py", "app/cli/train_hgb_v2.py")}})
    start = time.monotonic()
    ((X, y, meta), (VX, vy, vmeta)), provenance = load_development(root)
    model = make_hgb_v2(engineered=not args.raw_features)
    with threadpool_limits(limits=2):
        model.fit(X, y); scores = model.predict_proba(VX)[:, 1]
        joblib.dump(model, args.output / "model.joblib", compress=3)
        positions = np.random.default_rng(42).choice(len(VX), 128, replace=False)
        reload_scores = joblib.load(args.output / "model.joblib").predict_proba(VX.iloc[positions])[:, 1]
    metrics, queue = evaluate_scores(vmeta, vy, scores)
    metrics["error_groups"] = error_groups(vmeta, VX, vy, queue, train_users=set(meta["user_id"]))
    difference = float(np.max(np.abs(reload_scores - scores[positions])))
    if difference > 1e-12: raise ValueError("Control reload differs")
    np.save(args.output / "development-scores.npy", scores)
    report = {"status": "matched_hgb_development_evaluated", "final_test_read": False, "data": provenance,
              "fit_rows": len(y), "fit_positives": int(y.sum()), "development_rows": len(vy), "development_positives": int(vy.sum()),
              "metrics": metrics, "reload_max_diff": difference, "elapsed_seconds": round(time.monotonic() - start, 3),
              "artifact_sha256": {p.name: sha256_file(p) for p in args.output.iterdir() if p.is_file()}}
    write_json(args.output / "report.json", report)
    print(json.dumps({"directory": str(args.output), **report}, ensure_ascii=False, indent=2))


if __name__ == "__main__": main()
