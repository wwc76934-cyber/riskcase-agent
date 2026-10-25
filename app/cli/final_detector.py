"""Explicit freeze -> label-free predict -> one-time outcome evaluation -> audit."""

import argparse
import json
from pathlib import Path

from app.risk.final_detector import audit, evaluate, freeze, freeze_predictions
from app.risk.history import sha256_file


def main():
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("freeze", "predict", "evaluate", "audit"))
    parser.add_argument("--directory", type=Path, default=root / "runs/final-detector-v1")
    parser.add_argument("--recipes", type=Path)
    parser.add_argument("--features-manifest", type=Path, default=root / "data/features/features-b9692149f1c1/manifest.json")
    args = parser.parse_args()
    if args.action == "freeze":
        if args.recipes is None: parser.error("--recipes required for freeze")
        result = freeze(root, args.directory, json.loads(args.recipes.read_text(encoding="utf-8")), args.features_manifest)
    elif args.action == "predict": result = freeze_predictions(root, args.directory)
    elif args.action == "evaluate": result = evaluate(root, args.directory)
    else:
        result = audit(args.directory)
        previous = json.loads((args.directory / "report.json").read_text(encoding="utf-8"))
        if result != previous: raise ValueError("recomputed_final_report_differs")
        result = {"status": "independent_metrics_and_review_membership_verified", "report_sha256": sha256_file(args.directory / "report.json")}
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__": main()
