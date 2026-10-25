"""Same-case fixed workflow versus scripted/live investigator; development only."""

import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4

from app.cli.investigate_live import local_key
from app.llm.cost_ledger import CostLedger
from app.llm.offline_investigator import OfflineInvestigationClient
from app.llm.siliconflow_client import SiliconFlowClient, validate_policy
from app.risk.fixed_investigation import run_fixed_investigation
from app.risk.history import sha256_file
from app.risk.investigation import run_investigation
from app.risk.task_evaluation import build_development_pack, score_task
from app.tools.case_tools import CaseTools


def compare_task(task, *, client, client_mode):
    """Each arm gets a NEW tool instance over the identical immutable projection."""
    arms = {}
    for arm in ("fixed","investigator"):
        tools = CaseTools(Path(task["manifest"]),max_calls=6)
        started = time.perf_counter()
        if arm == "fixed":
            result = run_fixed_investigation(tools)
        else:
            try:
                result = run_investigation(client,tools,client_mode=client_mode,max_iterations=6,allow_format_repair=True)
            except KeyboardInterrupt:
                result = {"status":"cancelled","client_mode":client_mode,"client_requests":getattr(client,"sent_requests",0),
                    "tool_attempts":tools.attempts,"findings":None,"report_markdown":None,
                    "error":"Interrupted; sent requests may still be charged","evidence":tools.evidence,
                    "tool_trace":tools.trace,"messages":[]}
        seconds = round(time.perf_counter()-started,6)
        score = score_task(result,gold=task["gold"],required_checks=task["required_checks"])
        arms[arm] = {"result":result,"score":score,"elapsed_seconds":seconds,
            "projection_sha256":tools.manifest["projection_sha256"]}
    return arms


def main(argv=None):
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live",action="store_true",help="Run ONE selected development task on SiliconFlow")
    parser.add_argument("--prompt-key",action="store_true")
    parser.add_argument("--task-id",help="Required for live; offline omission runs all development tasks")
    args = parser.parse_args(argv)
    spec = root / "config/investigation-development-v1.json"
    spec_data = json.loads(spec.read_text(encoding="utf-8"))
    ids = {case["id"] for case in spec_data["cases"]}
    if args.task_id is not None and args.task_id not in ids: parser.error("Unknown development task ID")
    if args.live and args.task_id is None: parser.error("--live requires one explicit --task-id")
    if args.prompt_key and not args.live: parser.error("--prompt-key requires --live")
    policy = json.loads((root / "config/siliconflow-live-v1.json").read_text(encoding="utf-8"))
    validate_policy(policy)
    key = None
    if args.live:
        try:
            key = local_key(args.prompt_key)
        except ValueError:
            print("本地凭据暂不可读；请在普通 PowerShell 检查凭据配置。未发送请求。",file=sys.stderr)
            return 2
        if not key:
            print("未配置密钥。请先运行 python -m app.cli.credentials --set 一次保存。",file=sys.stderr)
            return 2
        if len(key) < 8 or any(char.isspace() for char in key):
            print("密钥格式不可用；未发送请求。",file=sys.stderr)
            return 2
    run_id = f"comparison-{'live' if args.live else 'offline'}-v1-{uuid4().hex[:12]}"
    directory = root / "runs" / run_id
    # Build the same frozen fixture pack; gold and fixture source are host-side only.
    tasks = build_development_pack(spec,directory)
    (directory / "evaluation-spec.json").write_bytes(spec.read_bytes())
    (directory / "policy.json").write_text(json.dumps(policy,indent=2)+"\n",encoding="utf-8")
    selected = [task for task in tasks if args.task_id is None or task["id"] == args.task_id]
    rows, artifacts = [], {}
    client = None
    try:
        client = SiliconFlowClient(key,policy=policy,ledger=CostLedger(root / "runs/llm-budget.sqlite"),run_id=run_id) if args.live else OfflineInvestigationClient()
        for task in selected:
            arms = compare_task(task,client=client,client_mode="siliconflow_live" if args.live else "SCRIPTED_NOT_REAL_LLM")
            for arm,record in arms.items():
                target = directory / task["id"] / arm
                target.mkdir()
                result = record.pop("result")
                if args.live: result = client.scrub(result)
                if result.get("report_markdown") is not None:
                    (target / "report.md").write_text(result.pop("report_markdown"),encoding="utf-8")
                (target / "trace.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
                for path in target.iterdir(): artifacts[str(path.relative_to(directory))] = sha256_file(path)
            rows.append({"task_id":task["id"],"arms":arms})
    finally:
        if args.live and client is not None:
            client.close()
            (directory / "usage.json").write_text(json.dumps(client.snapshot(),indent=2)+"\n",encoding="utf-8")
    for name in ("evaluation-spec.json","policy.json","usage.json"):
        if (directory / name).exists(): artifacts[name] = sha256_file(directory / name)
    for task in selected:
        for name in ("manifest.json","facts.sqlite"):
            path = Path(task["manifest"]).parent / name
            artifacts[str(path.relative_to(directory))] = sha256_file(path)
    summary = {"status":"completed","client_mode":"siliconflow_live" if args.live else "SCRIPTED_NOT_REAL_LLM",
        "split":"development_only","data_kind":spec_data["data_kind"],"spec_sha256":sha256_file(spec),
        "code_commit":subprocess.check_output(["git","rev-parse","HEAD"],cwd=root,text=True).strip(),
        "tasks":len(rows),"real_LLM_requests":client.sent_requests if args.live else 0,
        "arms":{arm:{"tasks_passed":sum(row["arms"][arm]["score"]["task_passed"] for row in rows),
            "tool_attempts":sum(row["arms"][arm]["score"]["tool_attempts"] for row in rows),
            "client_requests":sum(row["arms"][arm]["score"]["client_requests"] for row in rows),
            "submitted_checks":sum(row["arms"][arm]["score"]["submitted_checks"] for row in rows)} for arm in ("fixed","investigator")},
        "results":rows,"artifact_sha256":artifacts,
        "limits":["Authored development tasks; no holdout or business performance claim.",
            "Fixed arm always reports three checks; branch contract can allow investigator fewer checks. Query savings alone do not prove superior coverage or efficiency.",
            "Fixed arm uses zero LLM requests; scripted arm is NOT LLM ability.",
            "Local elapsed times are observations, not a latency benchmark. Live mode runs one task, at most six requests under existing estimated budgets."]}
    (directory / "comparison.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"output_directory":str(directory),**summary},ensure_ascii=False,indent=2))
    return 0 if all(summary["arms"][arm]["tasks_passed"] == len(rows) for arm in summary["arms"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
