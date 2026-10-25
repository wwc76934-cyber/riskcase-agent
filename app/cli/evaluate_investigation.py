"""Run authored DEVELOPMENT fixtures with a scripted client, no network or model fees."""

import json
from pathlib import Path
import subprocess
from uuid import uuid4

from app.llm.offline_investigator import OfflineInvestigationClient
from app.risk.history import sha256_file
from app.risk.investigation import run_investigation
from app.risk.task_evaluation import build_development_pack, score_task
from app.tools.case_tools import CaseTools


def main():
    root = Path(__file__).resolve().parents[2]
    spec = root / "config/investigation-development-v1.json"
    directory = root / "runs" / f"investigation-dev-v1-{uuid4().hex[:12]}"
    tasks = build_development_pack(spec,directory)
    (directory / "evaluation-spec.json").write_bytes(spec.read_bytes())
    scores = []
    for task in tasks:
        tools = CaseTools(Path(task["manifest"]))
        result = run_investigation(OfflineInvestigationClient(),tools,
            client_mode="SCRIPTED_DEVELOPMENT_FIXTURES_NOT_REAL_LLM",max_iterations=6)
        task_dir = Path(task["manifest"]).parent.parent
        if result.get("report_markdown") is not None:
            (task_dir / "report.md").write_text(result.pop("report_markdown"),encoding="utf-8")
        trace = task_dir / "trace.json"
        trace.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        score = score_task(result,gold=task["gold"],required_checks=task["required_checks"])
        scores.append({"task_id":task["id"],**score,"trace_sha256":sha256_file(trace),
            "case_manifest_sha256":sha256_file(Path(task["manifest"]))})
    summary = {"status":"completed","split":"development_only","client_mode":"SCRIPTED_NOT_REAL_LLM",
        "data_kind":"hand_constructed_synthetic_fixtures_NOT_IBM_samples",
        "spec_sha256":sha256_file(spec),"code_commit":subprocess.check_output(["git","rev-parse","HEAD"],cwd=root,text=True).strip(),
        "tasks":len(scores),"tasks_passed":sum(row["task_passed"] for row in scores),
        "required_checks":sum(row["required_checks"] for row in scores),
        "correct_required_checks":sum(row["correct_required_checks"] for row in scores),
        "wrong_or_unknown_claims":sum(row["wrong_or_unknown_claims"] for row in scores),
        "real_LLM_requests":0,"results":scores,
        "limits":["Authored development fixtures validate evaluator/control flow, not independent model ability.",
            "These are not IBM population samples, fraud labels, holdout tasks, or real-business performance.",
            "No fixed-workflow versus LLM advantage measured; no final test set accessed."]}
    (directory / "evaluation.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"output_directory":str(directory),**summary},ensure_ascii=False,indent=2))
    return 0 if summary["tasks_passed"] == len(scores) else 1


if __name__ == "__main__":
    raise SystemExit(main())
