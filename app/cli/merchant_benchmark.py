"""Frozen source-task batch, three arms, durable receipts and failure-inclusive scores."""

import argparse
from collections import Counter
import json
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4

from app.risk.history import sha256_file
from app.risk.merchant_evaluation import score_merchant
from app.risk.source_merchant_pack import build_source_pack,load_frozen_pack,write_json
from scripts.verify_merchant_research import verify


def aggregate(records):
    result={}
    for arm in ("fixed_scripted","evidence_only","agent"):
        rows=[r for r in records if r["arm"]==arm]
        result[arm]={"tasks":len(rows),"passed":sum(r.get("score",{}).get("task_passed",False) and r.get("source_sql_verified",False) for r in rows),
            "first_attempt_passed":sum(r.get("score",{}).get("first_attempt_passed") is True for r in rows),
            "known_requests":sum(r.get("real_LLM_requests") or 0 for r in rows),"unknown_request_slots":sum(r.get("real_LLM_requests") is None for r in rows),"tool_attempts":sum(r.get("tool_attempts",0) for r in rows),
            "cache_hits":sum(r.get("cache_hits",0) for r in rows),"successful_query_executions":sum(r.get("successful_query_executions",0) for r in rows),
            "seconds":round(sum(r.get("seconds",0) for r in rows),3),"statuses":dict(Counter(r["status"] for r in rows))}
    return result


def run_batch(root,pack,output,*,split,live=False):
    contract,tasks=load_frozen_pack(root,pack)
    if split not in ("development","evaluation"): raise ValueError("Explicit partition required")
    binding={"contract_sha256":sha256_file(pack / "contract.json"),"split":split,"live":live}
    if output.exists():
        if json.loads((output / "binding.json").read_text())!=binding: raise ValueError("Output belongs to a different experiment")
    else:
        output.mkdir(parents=True,exist_ok=False); write_json(output / "binding.json",binding)
    for task in (t for t in tasks if t["split"]==split):
        for arm in contract["arms"]:
            receipt=output / f"{task['id']}-{arm}.json"
            if receipt.exists(): continue  # Including failed/running: never silently repeat paid work.
            load_frozen_pack(root,pack)
            record={"task_id":task["id"],"arm":arm,"status":"running","score":{"task_passed":False,"first_attempt_passed":False},"real_LLM_requests":None if live and arm!="fixed_scripted" else 0}
            write_json(receipt,record)
            started=time.perf_counter()
            args=[sys.executable,"-X","utf8","-B","-m","app.cli.merchant_research","--case-manifest",task["manifest"],"--short-hours",str(task["question"]["short_hours"]),
                "--long-hours",str(task["question"]["long_hours"]),"--min-records",str(task["question"]["min_records"])]
            if arm=="evidence_only": args.append("--evidence-only")
            if arm=="agent": args.append("--memoize-tools")
            if live and arm!="fixed_scripted": args.append("--live")
            try:
                process=subprocess.run(args,cwd=root,capture_output=True,text=True,encoding="utf-8",timeout=210)
                summary=json.loads(process.stdout)
                directory=Path(summary["output_directory"])
                actual=json.loads((directory / "summary.json").read_text(encoding="utf-8"))
                record.update(status=actual["status"],summary_path=str(directory / "summary.json"),summary_sha256=sha256_file(directory / "summary.json"),
                    score=score_merchant(actual,task["gold"]),real_LLM_requests=actual["real_LLM_requests"],tool_attempts=actual["tool_attempts"],cache_hits=actual["cache_hits"],
                    successful_query_executions=actual["successful_query_executions"],failure_stage=actual["failure_stage"],source_sql_verified=False)
                if actual["status"]=="completed_validated":
                    verify(directory / "summary.json",Path(contract["cohort_report"]));record["source_sql_verified"]=True
                if (directory / "usage.json").exists():
                    usage=json.loads((directory / "usage.json").read_text())
                    record["estimated_cny"]=usage["ledger"]["run_committed_estimate_cny"]
            except KeyboardInterrupt:
                record["status"]="cancelled_unknown_charge";write_json(receipt,record);raise
            except Exception as exc:
                record.update(status="failed_or_interrupted",error_type=type(exc).__name__)
                record["score"]["task_passed"]=False
            record["seconds"]=round(time.perf_counter()-started,6)
            write_json(receipt,record)
            print(json.dumps({"task":task["id"],"arm":arm,"status":record["status"],"passed":record["score"]["task_passed"]},ensure_ascii=False),flush=True)
    records=[json.loads(p.read_text(encoding="utf-8")) for p in sorted(output.glob("u*.json"))]
    report={**binding,"status":"batch_attempts_recorded","expected_slots":sum(t["split"]==split for t in tasks)*3,"recorded_slots":len(records),
        "aggregate":aggregate(records),"records":records,"limits":contract["limits"]}
    write_json(output / "report.json",report)
    return report


def main():
    root=Path(__file__).resolve().parents[2]
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action",choices=("build","run"))
    parser.add_argument("--pack",type=Path)
    parser.add_argument("--output",type=Path)
    parser.add_argument("--split",choices=("development","evaluation"),default="development")
    parser.add_argument("--live",action="store_true")
    args=parser.parse_args()
    pack=args.pack or root / "runs" / f"source-merchant-v1-{uuid4().hex[:12]}"
    if args.action=="build":
        contract=build_source_pack(root,pack)
        print(json.dumps({"pack":str(pack),"contract_sha256":sha256_file(pack / "contract.json"),"tasks":contract["tasks"],"users":contract["users"]},indent=2))
    else:
        if args.pack is None or args.output is None: parser.error("run requires --pack and --output")
        report=run_batch(root,pack,args.output,split=args.split,live=args.live)
        print(json.dumps(report["aggregate"],indent=2))


if __name__=="__main__": main()
