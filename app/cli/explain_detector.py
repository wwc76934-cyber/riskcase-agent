"""Explain a bound neural alert with audited single-input sensitivity, never causal claims."""

import argparse
import json
from pathlib import Path
from uuid import uuid4

from app.risk.detector_explanation import FrozenNeuralEnsemble, _json_file, explain_case, load_feature_input, models_from_neural_run, render_explanation
from app.risk.history import sha256_file
from app.risk.source_merchant_pack import write_json


def main():
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case-manifest", required=True, type=Path)
    parser.add_argument("--neural-run", required=True, type=Path)
    parser.add_argument("--seeds", nargs="+", type=int, default=[42, 43, 44])
    parser.add_argument("--trust-local-models", action="store_true", help="Only load models you produced locally; pickle can execute code")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input-json", type=Path, help="Explicit bound MODEL_COLUMNS input, never label fields")
    source.add_argument("--features-manifest", type=Path, help="Read one bound row from the frozen label-free feature database")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if len(set(args.seeds)) != len(args.seeds) or not 1 <= len(args.seeds) <= 3: parser.error("one to three distinct seeds required")
    model = FrozenNeuralEnsemble(models_from_neural_run(args.neural_run, seeds=args.seeds), trusted_local=args.trust_local_models)
    if args.features_manifest: bound, proof = load_feature_input(args.features_manifest, args.case_manifest)
    else: bound, proof = _json_file(args.input_json), None
    result = explain_case(args.case_manifest, bound, model, model_metadata=model.metadata, input_provenance=proof)
    directory = args.output or root / "runs" / ("detector-explanation-" + uuid4().hex[:12])
    directory.mkdir(parents=True, exist_ok=False)
    write_json(directory / "bound-input.json", bound)
    write_json(directory / "explanation.json", result)
    (directory / "report.md").write_text(render_explanation(result), encoding="utf-8")
    write_json(directory / "manifest.json", {"status": "sensitivity_exported", "evidence_id": result["evidence_id"],
               "artifact_sha256": {name: sha256_file(directory/name) for name in ("bound-input.json", "explanation.json", "report.md")},
               "code_file_sha256": {name: sha256_file(root/name) for name in (
                   "app/risk/detector_explanation.py", "app/cli/explain_detector.py", "app/risk/neural_v2.py",
                   "app/risk/ml_models.py", "app/tools/case_tools.py", "app/risk/case_snapshot.py", "app/risk/history.py")},
               "labels_read": False, "causal": False, "policy_approved": False, "deployed": False})
    print(json.dumps({"output_directory": str(directory), "evidence_id": result["evidence_id"], "original_score": result["original"]["score"],
                      "scored_perturbations": sum(r["status"] == "scored" for r in result["perturbations"]), "causal": False}, ensure_ascii=False, indent=2))


if __name__ == "__main__": main()
