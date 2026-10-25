"""Build eight authored development tasks and check the offline control pipeline."""

import json
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

from app.risk.history import sha256_file
from app.risk.merchant_evaluation import build_merchant_pack, score_merchant


def main():
    root = Path(__file__).resolve().parents[2]
    directory = root / "runs" / f"merchant-development-v1-{uuid4().hex[:12]}"
    spec = root / "config/merchant-development-v1.json"
    tasks = build_merchant_pack(spec,directory / "pack")
    records = []
    for task in tasks:
        process = subprocess.run([sys.executable,"-X","utf8","-B","-m","app.cli.merchant_research","--case-manifest",task["manifest"],
            "--short-hours",str(task["question"]["short_hours"]),"--long-hours",str(task["question"]["long_hours"]),"--min-records",str(task["question"]["min_records"])],
            cwd=root,text=True,capture_output=True,encoding="utf-8",timeout=120)
        if process.returncode:
            records.append({"id":task["id"],"score":{"task_passed":False,"correct_metrics":0,"required_metrics":2,"run_status":"process_failed"}})
            continue
        run = json.loads(process.stdout)
        path = Path(run["output_directory"])
        verification = subprocess.run([sys.executable,"-X","utf8","-B","-m","scripts.verify_merchant_research","--summary",str(path / "summary.json"),"--cohort-report",task["cohort_report"]],
            cwd=root,text=True,capture_output=True,encoding="utf-8",timeout=120)
        score = score_merchant(run,task["gold"])
        if verification.returncode: score["task_passed"]=False
        records.append({"id":task["id"],"summary_path":str(path / "summary.json"),"summary_sha256":sha256_file(path / "summary.json"),
            "score":score,"source_sql_verified":verification.returncode==0})
    report = {"status":"development_control_completed","client_mode":"SCRIPTED_NOT_REAL_LLM","spec_sha256":sha256_file(spec),
        "tasks":len(records),"passed":sum(record["score"]["task_passed"] for record in records),"real_LLM_requests":0,
        "records":records,"limits":["Authored development fixtures, not IBM cases or held-out evaluation.","Scripted control passing is not evidence of model capability or Agent advantage."]}
    (directory / "report.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"output_directory":str(directory),**report},indent=2))
    return 0 if report["passed"]==report["tasks"] else 1


if __name__=="__main__": raise SystemExit(main())
