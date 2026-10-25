"""One typed merchant research task against a bound synthetic development case."""

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import shutil
import subprocess
from uuid import uuid4

from app.cli.investigate_live import local_key
from app.llm.cost_ledger import CostLedger
from app.llm.siliconflow_client import SiliconFlowClient
from app.risk.history import sha256_file
from app.risk.merchant_research import MerchantQuestion, MerchantResearchTools, MemoizedMerchantTools, PreloadedMerchantTools, PreloadedScriptedClient, ScriptedMerchantClient, run_merchant_research
from app.tools.case_tools import CaseTools


def main(argv=None):
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live",action="store_true")
    parser.add_argument("--no-format-repair",action="store_true",help="Disable the single budgeted presentation correction")
    parser.add_argument("--evidence-only",action="store_true",help="Fixed host queries, then no model-controlled tools")
    parser.add_argument("--memoize-tools",action="store_true",help="Cache repeated successful queries within this immutable case")
    parser.add_argument("--case-manifest",type=Path,default=root / "runs/case-v1-f4a58a923f32/case/manifest.json")
    parser.add_argument("--short-hours",type=int,default=168)
    parser.add_argument("--long-hours",type=int,default=720)
    parser.add_argument("--min-records",type=int,default=10)
    args = parser.parse_args(argv)
    question = MerchantQuestion(args.short_hours,args.long_hours,args.min_records)
    source = CaseTools(args.case_manifest,allow_merchant_distribution=True)
    if not "2018-01-01" <= source.manifest["cutoff"] < "2019-01-01":
        raise ValueError("Development cases only")
    key = local_key() if args.live else None
    if args.live and not key: raise ValueError("Saved project credential unavailable; no request sent")
    identity = f"merchant-research-{'live' if args.live else 'offline'}-v1-{uuid4().hex[:12]}"
    directory = root / "runs" / identity
    (directory / "case").mkdir(parents=True,exist_ok=False)
    for name in ("manifest.json","facts.sqlite"):
        shutil.copyfile(args.case_manifest.parent / name,directory / "case" / name)
    tools = PreloadedMerchantTools(directory / "case/manifest.json",question) if args.evidence_only else (MemoizedMerchantTools if args.memoize_tools else MerchantResearchTools)(directory / "case/manifest.json")
    policy = json.loads((root / "config/siliconflow-live-v1.json").read_text(encoding="utf-8"))
    mode = "siliconflow_live" if args.live else "SCRIPTED_NOT_REAL_LLM"
    client = SiliconFlowClient(key,policy=policy,ledger=CostLedger(root / "runs/llm-budget.sqlite"),run_id=identity) if args.live else PreloadedScriptedClient(question,tools.evidence) if args.evidence_only else ScriptedMerchantClient(question)
    (directory / "question.json").write_text(json.dumps(asdict(question),indent=2)+"\n",encoding="utf-8")
    (directory / "policy.json").write_text(json.dumps(policy,indent=2)+"\n",encoding="utf-8")
    try:
        result = run_merchant_research(client,tools,question,client_mode=mode,max_requests=policy["max_requests"],allow_format_repair=not args.no_format_repair,evidence_preloaded=args.evidence_only)
    finally:
        if args.live:
            client.close()
            (directory / "usage.json").write_text(json.dumps(client.snapshot(),indent=2)+"\n",encoding="utf-8")
    if args.live: result = client.scrub(result)
    report = result.pop("report_markdown")
    if report is not None: (directory / "report.md").write_text(report,encoding="utf-8")
    (directory / "trace.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    code = ["app/cli/merchant_research.py","app/risk/merchant_research.py","app/risk/merchant_stats.py","app/tools/case_tools.py"]
    summary = {"status":result["status"],"client_mode":mode,"data_kind":"synthetic","question":asdict(question),
        "findings":result["findings"],"tool_attempts":tools.attempts,"client_requests":result["client_requests"],
        "first_attempt":result["first_attempt"],"format_repair":result["format_repair"],"failure_stage":result["failure_stage"],
        "format_repair_enabled":not args.no_format_repair,"maximum_client_requests":policy["max_requests"],
        "evidence_preloaded":args.evidence_only,"memoization_enabled":args.memoize_tools or args.evidence_only,
        "cache_hits":sum(row.get("cache_hit",False) for row in tools.trace),
        "successful_query_executions":sum(row["result"]["ok"] and not row.get("cache_hit",False) for row in tools.trace),
        "real_LLM_requests":client.sent_requests if args.live else 0,
        "code_commit":subprocess.check_output(["git","rev-parse","HEAD"],cwd=root,text=True).strip(),
        "code_file_sha256":{name:sha256_file(root / name) for name in code},
        "artifact_sha256":{str(path.relative_to(directory)).replace("\\","/"):sha256_file(path) for path in directory.rglob("*") if path.is_file()},
        "limits":["Synthetic development case; not held-out task evaluation or business validation.",
            "Nested windows, source merchant IDs, positive-only amount shares; no fraud verdict.",
            "Minimum sample policy is experimental, not a calibrated risk threshold."]}
    (directory / "summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"output_directory":str(directory),**summary},ensure_ascii=False,indent=2))
    return 0 if result["status"]=="completed_validated" else 1


if __name__ == "__main__": raise SystemExit(main())
