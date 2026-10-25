"""Recheck saved same-case comparisons, citations and task counts without API calls."""

import argparse
from decimal import Decimal
import json
from pathlib import Path

from app.risk.history import sha256_file
from app.risk.investigation import render_report, validate_findings


def verify(path):
    directory = path.parent
    summary = json.loads(path.read_text(encoding="utf-8"))
    for name,digest in summary["artifact_sha256"].items():
        if sha256_file(directory / name) != digest: raise ValueError(f"Changed artifact: {name}")
    spec_path = directory / "evaluation-spec.json"
    if sha256_file(spec_path) != summary["spec_sha256"]: raise ValueError("Changed task contract")
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    checks = ("high_prior_frequency_1h","amount_above_prior_positive_max_30d","merchant_id_unseen_30d")
    tasks = {case["id"]:case for case in spec["cases"]}
    totals = {arm:{"tasks_passed":0,"tool_attempts":0,"client_requests":0,"submitted_checks":0} for arm in ("fixed","investigator")}
    seen = set()
    for row in summary["results"]:
        identity = row["task_id"]
        if identity in seen: raise ValueError("Repeated task")
        seen.add(identity)
        case = tasks[identity]
        gold = dict(zip(checks,case["gold"]))
        required = case.get("required_checks",list(checks))
        manifest = json.loads((directory / identity / "case/manifest.json").read_text(encoding="utf-8"))
        projection = sha256_file(directory / identity / "case/facts.sqlite")
        if projection != manifest["projection_sha256"]: raise ValueError("Changed case")
        for arm in totals:
            record = row["arms"][arm]
            trace = json.loads((directory / identity / arm / "trace.json").read_text(encoding="utf-8"))
            if record["projection_sha256"] != projection: raise ValueError("Different permissions/snapshot across arms")
            if trace["tool_attempts"] != len(trace["tool_trace"]): raise ValueError("Tool attempt count mismatch")
            findings = (trace.get("findings") or {}).get("findings",[])
            predictions = {finding["check"]:finding["assessment"] for finding in findings}
            wrong = len(findings)-len(predictions)+sum(check not in gold or status!=gold[check] for check,status in predictions.items())
            correct = sum(predictions.get(check)==gold[check] for check in required)
            passed = trace["status"] == "completed_validated" and correct == len(required) and wrong == 0
            score = record["score"]
            expected = {"task_passed":passed,"required_checks":len(required),"correct_required_checks":correct,
                "covered_checks":sum(check in predictions for check in required),"submitted_checks":len(findings),
                "wrong_or_unknown_claims":wrong,"run_status":trace["status"],"client_requests":trace["client_requests"],
                "tool_attempts":trace["tool_attempts"],"format_repair_attempted":trace.get("format_repair",{}).get("attempted",False)}
            if score != expected: raise ValueError("Saved task score disagrees with authored gold/trace")
            if trace["status"] == "completed_validated":
                body = validate_findings(json.dumps(trace["findings"]),trace["evidence"])
                if (directory / identity / arm / "report.md").read_text(encoding="utf-8") != render_report(body,trace["evidence"]):
                    raise ValueError("Report does not match validated evidence")
            totals[arm]["tasks_passed"] += passed
            for name in ("tool_attempts","client_requests","submitted_checks"): totals[arm][name] += score[name]
    if len(seen) != summary["tasks"] or totals != summary["arms"]: raise ValueError("Aggregate comparison mismatch")
    if summary["client_mode"] == "SCRIPTED_NOT_REAL_LLM" and summary["real_LLM_requests"] != 0:
        raise ValueError("Scripted requests mislabeled as real")
    if summary["client_mode"] == "siliconflow_live":
        audit = json.loads((directory / "usage.json").read_text(encoding="utf-8"))
        policy = json.loads((directory / "policy.json").read_text(encoding="utf-8"))
        requests,entries = audit["request_audit"],audit["ledger"]["entries"]
        if not len(requests)==len(entries)==audit["requests_sent"]==summary["real_LLM_requests"]:
            raise ValueError("Live request counts disagree")
        estimate,unknown = Decimal(0),0
        for request,entry in zip(requests,entries):
            if request["reservation_id"] != entry["reservation_id"]: raise ValueError("Live reservation mismatch")
            if entry["estimated_cny"] is None:
                estimate += Decimal(entry["reserved_cny"])
                unknown += 1
            else:
                usage = entry["usage"]
                if usage != request["usage"] or usage["total_tokens"] != usage["prompt_tokens"]+usage["completion_tokens"]:
                    raise ValueError("Live usage mismatch")
                cost = (Decimal(usage["prompt_tokens"])*Decimal(policy["input_rate_cny_per_million"])
                    +Decimal(usage["completion_tokens"])*Decimal(policy["output_rate_cny_per_million"]))/Decimal(1000000)
                if cost != Decimal(entry["estimated_cny"]): raise ValueError("Live fee estimate mismatch")
                estimate += cost
        if estimate != Decimal(audit["ledger"]["run_committed_estimate_cny"]) or unknown != audit["ledger"]["unknown_cost_requests"]:
            raise ValueError("Live fee total mismatch")
    result = {"status":"passed","summary_sha256":sha256_file(path),"tasks_checked":len(seen),"arms":totals,
        "limits":"Artifacts, gold scores and citations checked; no business generalization, independent source SQL or provider invoice claim."}
    (directory / "comparison-verification.json").write_text(json.dumps(result,indent=2)+"\n",encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary",required=True,type=Path)
    print(json.dumps(verify(parser.parse_args().summary),indent=2))
