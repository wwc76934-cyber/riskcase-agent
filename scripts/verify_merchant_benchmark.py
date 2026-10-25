"""Reconcile every frozen task/arm slot, receipt, score, artifact and successful SQL proof."""

import argparse
from decimal import Decimal
import json
from pathlib import Path

from app.cli.merchant_benchmark import aggregate
from app.risk.history import sha256_file
from app.risk.merchant_evaluation import score_merchant
from app.risk.source_merchant_pack import load_frozen_pack,write_json
from scripts.verify_merchant_research import verify


def verify_batch(root,pack,output):
    contract,tasks=load_frozen_pack(root,pack)
    binding=json.loads((output / "binding.json").read_text())
    report=json.loads((output / "report.json").read_text(encoding="utf-8"))
    if binding["contract_sha256"]!=sha256_file(pack / "contract.json") or any(report[k]!=v for k,v in binding.items()): raise ValueError("Batch binding differs")
    selected={t["id"]:t for t in tasks if t["split"]==binding["split"]}
    expected={(identity,arm) for identity in selected for arm in contract["arms"]}
    records=[json.loads(p.read_text(encoding="utf-8")) for p in sorted(output.glob("u*.json"))]
    if len(records)!=len(expected) or {(r["task_id"],r["arm"]) for r in records}!=expected: raise ValueError("Missing/duplicate/outside task slots")
    if any(r["status"]=="running" for r in records): raise ValueError("Interrupted slot needs explicit resolution; not a completed batch")
    if report["records"]!=records or report["aggregate"]!=aggregate(records): raise ValueError("Aggregate differs from receipts")
    if report["expected_slots"]!=len(expected) or report["recorded_slots"]!=len(records): raise ValueError("Slot counts differ")
    verified=failed=0
    estimates=Decimal(0)
    for record in records:
        if "summary_path" not in record:
            if record["score"]["task_passed"]: raise ValueError("Missing evidence marked as passed")
            failed+=1;continue
        path=Path(record["summary_path"])
        if not path.resolve().is_relative_to((root / "runs").resolve()) or sha256_file(path)!=record["summary_sha256"]: raise ValueError("Summary changed or outside run root")
        summary=json.loads(path.read_text(encoding="utf-8"))
        task=selected[record["task_id"]]
        if summary["question"]!=task["question"]: raise ValueError("Question differs from frozen task")
        manifest=json.loads((path.parent / "case/manifest.json").read_text())
        if manifest["target_transaction_id"]!=task["transaction_id"] or manifest["user_id"]!=task["user_id"] or manifest["projection_sha256"]!=task["projection_sha256"]: raise ValueError("Task source binding differs")
        for name,digest in summary["artifact_sha256"].items():
            artifact=(path.parent / name).resolve()
            if not artifact.is_relative_to(path.parent.resolve()) or sha256_file(artifact)!=digest: raise ValueError("Run artifact changed")
        expected_live=binding["live"] and record["arm"]!="fixed_scripted"
        if (summary["client_mode"]=="siliconflow_live")!=expected_live: raise ValueError("Arm model mode differs")
        if summary["evidence_preloaded"]!=(record["arm"]=="evidence_only"): raise ValueError("Arm evidence access differs")
        for key in ("real_LLM_requests","tool_attempts","cache_hits","successful_query_executions"):
            if record[key]!=summary[key]: raise ValueError("Run accounting differs")
        if record["score"]!=score_merchant(summary,task["gold"]): raise ValueError("Task score differs from host reference")
        if summary["status"]=="completed_validated":
            verify(path,Path(contract["cohort_report"]));verified+=1
            if not record["source_sql_verified"]: raise ValueError("Successful SQL proof missing")
        else:
            failed+=1
            if (path.parent / "report.md").exists() or record["score"]["task_passed"]: raise ValueError("Rejected result published as success")
        if expected_live:
            usage=json.loads((path.parent / "usage.json").read_text())
            estimate=usage["ledger"]["run_committed_estimate_cny"]
            if estimate!=record["estimated_cny"]: raise ValueError("Cost receipt differs")
            estimates+=Decimal(estimate)
    proof={"status":"batch_receipts_verified","report_sha256":sha256_file(output / "report.json"),"contract_sha256":binding["contract_sha256"],
        "slots":len(records),"successful_source_sql_proofs":verified,"failed_slots_retained":failed,"estimated_cny":str(estimates),
        "receipt_sha256":{p.name:sha256_file(p) for p in sorted(output.glob("u*.json"))},
        "limits":["Failed model answers remain failures; their artifact hashes are checked, not reclassified as SQL-validated reports.","Source is synthetic; this is not an external financial effectiveness audit."]}
    write_json(output / "verification.json",proof)
    return proof


def main():
    root=Path(__file__).resolve().parents[1]
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    result=verify_batch(root,args.pack,args.output)
    print(json.dumps({k:v for k,v in result.items() if k!="receipt_sha256"},indent=2))


if __name__=="__main__": main()
