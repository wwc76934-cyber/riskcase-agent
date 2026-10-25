"""Re-open every frozen slot, source-join artifacts and independently count outcomes."""

import argparse
from decimal import Decimal
import json
from pathlib import Path

from app.risk.history import sha256_file
from app.risk.mixed_pack import load
from app.risk.mixed_sql import SourceAudit
from app.risk.mixed_investigation import MixedQuestion, check_answer, query_plan, render_report
from app.risk.source_merchant_pack import write_json
from scripts.verify_mixed import verify


def verify_batch(root, pack, directory, *, frozen_code_root=None):
    code_root = frozen_code_root or root
    contract, tasks = load(code_root, pack)
    report = json.loads((directory / "report.json").read_text(encoding="utf-8"))
    if report["contract_sha256"] != sha256_file(pack / "contract.json"): raise ValueError("Wrong frozen contract")
    selected = {task["id"]: task for task in tasks if task["split"] == report["split"]}
    expected = {(identity, arm) for identity in selected for arm in contract["arms"]}
    actual = [(r["task_id"], r["arm"]) for r in report["records"]]
    if len(actual) != len(set(actual)) or set(actual) != expected or report["recorded_slots"] != len(expected): raise ValueError("Missing/extra/duplicate slot")
    counts = {arm: {"tasks": 0, "passed": 0, "first_attempt_passed": 0, "real_requests": 0,
                    "unknown_request_slots": 0, "tool_attempts": 0, "extra_distinct_queries": 0,
                    "estimated_cny": Decimal(0), "seconds": 0} for arm in contract["arms"]}
    successful_observations = validated_reports = sql_slots = 0
    nonconformant = []
    with SourceAudit(Path(contract["cohort_report"])) as audit:
        for record in report["records"]:
            identity, arm = record["task_id"], record["arm"]
            if json.loads((directory / f"{identity}-{arm}.json").read_text(encoding="utf-8")) != record: raise ValueError("Receipt differs from report")
            total = counts[arm]; total["tasks"] += 1; total["seconds"] += record.get("seconds", 0)
            if record.get("real_requests") is None: total["unknown_request_slots"] += 1
            else: total["real_requests"] += record["real_requests"]
            total["estimated_cny"] += Decimal(record.get("estimated_cny", "0"))
            total["tool_attempts"] += record.get("tool_attempts", 0)
            if "summary_path" not in record:
                if record.get("proof", {}).get("task_passed"): raise ValueError("Pass lacks artifacts")
                continue
            summary_path = Path(record["summary_path"]).resolve()
            if not summary_path.is_relative_to(directory.resolve()) or sha256_file(summary_path) != record["summary_sha256"]: raise ValueError("Summary changed/outside batch")
            summary = json.loads(summary_path.read_text(encoding="utf-8")); task = selected[identity]
            expected_audit_rejection = record["status"] == "failed_or_interrupted" and summary["tool_attempts"] > 6
            if summary["arm"] != arm or summary["question"] != task["question"] or (summary["status"] != record["status"] and not expected_audit_rejection): raise ValueError("Task/arm/status binding differs")
            if summary["artifact_sha256"]["case/manifest.json"] != task["manifest_sha256"] or summary["artifact_sha256"]["case/facts.sqlite"] != task["projection_sha256"]:
                raise ValueError("Case or budget policy differs")
            # Runtime persists parsed JSON; Windows source line endings need not match.
            if json.loads((summary_path.parent / "policy.json").read_text(encoding="utf-8")) != json.loads((code_root / "config/siliconflow-live-v1.json").read_text(encoding="utf-8")):
                raise ValueError("Budget policy values differ")
            trace = json.loads((summary_path.parent / "trace.json").read_text(encoding="utf-8"))
            if expected_audit_rejection:
                # Preserve the frozen failed receipt. Check its evidence independently,
                # but never promote a numerically correct over-budget artifact to a pass.
                for name, expected_hash in summary["artifact_sha256"].items():
                    path = (summary_path.parent / name).resolve()
                    if not path.is_relative_to(summary_path.parent) or sha256_file(path) != expected_hash: raise ValueError("Rejected artifact changed")
                manifest = json.loads((summary_path.parent / "case/manifest.json").read_text(encoding="utf-8"))
                audit.projection(manifest, summary_path.parent / "case/facts.sqlite")
                evidence, successful = {}, 0
                for entry in trace["tool_trace"]:
                    if entry["result"]["ok"]:
                        expected_data = audit.observation(manifest, entry["tool"], entry["arguments"])
                        if expected_data != entry["result"]["data"]: raise ValueError("Rejected task SQL evidence differs")
                        evidence[expected_data["evidence_id"]] = expected_data; successful += 1
                if successful > 6 or evidence != trace["evidence"]: raise ValueError("Read beyond execution budget")
                question = MixedQuestion(**task["question"])
                if summary["status"] == "completed_validated":
                    body, stage = check_answer(json.dumps(trace["findings"]), evidence, question)
                    if stage is not None or {r["check"]:r["assessment"] for r in body["findings"]} != task["gold"] or render_report(body,evidence,question) != (summary_path.parent / "report.md").read_text(encoding="utf-8"):
                        raise ValueError("Rejected numerical report differs")
                elif trace["findings"] is not None or (summary_path.parent / "report.md").exists(): raise ValueError("Failed task unexpectedly published report")
                needed = {(name,json.dumps(args,sort_keys=True)) for name,args in query_plan(question)}
                actual_queries = {(e['tool'],json.dumps(e['arguments'],sort_keys=True)) for e in trace['tool_trace'] if e['result']['ok']}
                proof = {"task_passed":False,"first_attempt_passed":False,"answer_published":False,"extra_distinct_queries":len(actual_queries-needed)}
                nonconformant.append({"task_id":identity,"arm":arm,"reason":"tool_attempt_policy_exceeded",
                                     "attempts":trace['tool_attempts'],"successful_queries":successful,"raw_report_exists":(summary_path.parent / 'report.md').exists(),
                                     "frozen_receipt_unchanged":True})
            else:
                proof = verify(summary_path, audit)
                if proof != record.get("proof") or proof["gold"] != task["gold"]: raise ValueError("SQL proof differs")
            successful_observations += sum(e["result"]["ok"] for e in trace["tool_trace"])
            sql_slots += 1; validated_reports += proof["answer_published"]
            total["passed"] += proof["task_passed"]; total["first_attempt_passed"] += proof["first_attempt_passed"]
            total["extra_distinct_queries"] += proof["extra_distinct_queries"]
            if record["real_requests"] != summary["real_LLM_requests"]: raise ValueError("Request count differs")
            if report["live"] and arm != "fixed":
                usage = json.loads((summary_path.parent / "usage.json").read_text(encoding="utf-8"))
                if usage["requests_sent"] != record["real_requests"] or usage["ledger"]["run_committed_estimate_cny"] != record["estimated_cny"]: raise ValueError("Usage differs")
            elif record["real_requests"] != 0: raise ValueError("Offline/fixed slot sent real requests")
    for arm, row in counts.items():
        for key in ("tasks", "passed", "first_attempt_passed", "real_requests", "unknown_request_slots", "tool_attempts", "extra_distinct_queries"):
            if row[key] != report["aggregate"][arm][key]: raise ValueError("Aggregate mismatch: " + arm + "/" + key)
        if row["estimated_cny"] != Decimal(report["aggregate"][arm]["estimated_cny"]): raise ValueError("Cost aggregate mismatch")
        row["estimated_cny"] = str(row["estimated_cny"])
        row["seconds"] = round(row["seconds"], 3)
    proof = {"status": "all_slots_independently_verified", "report_sha256": sha256_file(directory / "report.json"),
             "contract_sha256": sha256_file(pack / "contract.json"), "expected_slots": len(expected), "source_sql_verified_slots": sql_slots,
             "successful_observations_checked": successful_observations, "published_validated_reports": validated_reports,
             "failed_slots_retained": len(expected)-validated_reports, "counts": counts, "labels_opened": False, "final_2019_test_opened": False}
    proof["nonconformant_report_artifacts"] = nonconformant
    write_json(directory / "batch-verification.json", proof)
    return proof


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", type=Path, required=True); parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--frozen-code-root",type=Path,help="Explicit code-hash snapshot for historical runs after a later fix")
    args = parser.parse_args()
    print(json.dumps(verify_batch(root, args.pack, args.directory, frozen_code_root=args.frozen_code_root), indent=2))


if __name__ == "__main__": main()
