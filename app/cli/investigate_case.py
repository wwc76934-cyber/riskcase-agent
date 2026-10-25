"""Project one frozen development alert and run an explicitly offline investigation."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
from uuid import uuid4

from app.llm.offline_investigator import OfflineInvestigationClient
from app.risk.case_snapshot import export_case
from app.risk.history import sha256_file
from app.risk.investigation import run_investigation
from app.tools.case_tools import CaseTools


def main():
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--detector-report", type=Path, default=root / "runs/ml-v1-90f95014b365/report.json")
    parser.add_argument("--transaction-id", help="Must already occur in the frozen tree review queue")
    args = parser.parse_args()
    report = json.loads(args.detector_report.read_text(encoding="utf-8"))
    frozen_path = args.detector_report.parent / "frozen-experiment.json"
    queue_path = args.detector_report.parent / "daily-review.jsonl"
    for path in (frozen_path, queue_path):
        if sha256_file(path) != report["artifact_sha256"][path.name]:
            raise ValueError("Detector input artifact changed")
    if report["status"] != "validation_models_evaluated" or report["final_test_read"]:
        raise ValueError("Frozen development detector report required")
    frozen = json.loads(frozen_path.read_text(encoding="utf-8"))
    manifest_path = Path(frozen["features_manifest"])
    if sha256_file(manifest_path) != frozen["features_manifest_sha256"]:
        raise ValueError("Feature manifest changed")
    features = json.loads(manifest_path.read_text(encoding="utf-8"))
    cohort_path = Path(features["cohort_report"])
    if sha256_file(cohort_path) != features["cohort_report_sha256"]:
        raise ValueError("Cohort changed")
    cohort = json.loads(cohort_path.read_text(encoding="utf-8"))
    selected = None
    with queue_path.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            if row["strategy"] == "hist_gradient_boosting" and (args.transaction_id is None or row["transaction_id"] == args.transaction_id):
                selected = row
                break
    if selected is None:
        raise ValueError("Transaction not found in frozen tree review queue")
    directory = root / "runs" / f"case-v1-{uuid4().hex[:12]}"
    directory.mkdir(parents=True, exist_ok=False)
    alert = {"transaction_id": selected["transaction_id"], "score": selected["score"],
        "detector": f"hist_gradient_boosting/{args.detector_report.parent.name}",
        "detector_model_sha256": report["strategies"]["hist_gradient_boosting"]["model_sha256"],
        "detector_report_sha256": sha256_file(args.detector_report),
        "selection": "first matching entry in frozen chronological daily top-10 queue; no label access",
        "day_rank": selected["day_rank"], "score_interpretation": "uncalibrated synthetic-development ranking score"}
    try:
        case_path = export_case(directory / "case", source_database=cohort_path.parent / "dataset/facts.sqlite",
            expected_source_sha256=cohort["artifact_sha256"]["dataset/facts.sqlite"], transaction_id=selected["transaction_id"],
            alert=alert, coverage_start=cohort["coverage"]["warmup_start"], coverage_end=cohort["coverage"]["end"],
            full_source_scanned=cohort["source_csv_fully_scanned"], source_report_sha256=sha256_file(cohort_path))
        tools = CaseTools(case_path, max_calls=6)
        if tools.target["event_time"] != selected["event_time"]:
            raise ValueError("Alert timestamp does not match the bound source transaction")
        result = run_investigation(OfflineInvestigationClient(), tools,
            client_mode="scripted_offline_policy_NOT_LLM", max_iterations=8)
        if result["report_markdown"] is not None:
            (directory / "report.md").write_text(result.pop("report_markdown"), encoding="utf-8")
        (directory / "trace.json").write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
        summary = {"status": result["status"], "client_mode": result["client_mode"],
            "created_at": datetime.now(timezone.utc).isoformat(), "data_kind": "synthetic",
            "transaction_id": selected["transaction_id"], "client_requests": result["client_requests"],
            "tool_attempts": result["tool_attempts"], "real_LLM_requests": 0,
            "findings": result["findings"], "code_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
            "artifact_sha256": {name: sha256_file(directory / name) for name in (
                ["case/manifest.json", "case/facts.sqlite", "trace.json"] + (["report.md"] if (directory / "report.md").exists() else []))},
            "limits": ["Scripted policy checks orchestration, not LLM investigation ability.",
                "One development alert smoke test, not task accuracy or efficiency evidence.",
                "No label store opened by this CLI or its tools.", "Real-model, task evaluation and production isolation remain incomplete."]}
        (directory / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
        print(json.dumps({"output_directory": str(directory), **summary}, ensure_ascii=False, indent=2))
    except Exception as exc:
        (directory / "failed.json").write_text(json.dumps({"status": "failed", "error_type": type(exc).__name__})+"\n", encoding="utf-8")
        raise


if __name__ == "__main__":
    main()
