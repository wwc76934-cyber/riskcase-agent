"""Recompute intent scores and audit all conditional financial evidence from source SQL."""

import argparse
from collections import Counter
from decimal import Decimal
import json
from pathlib import Path

from app.cli.natural_benchmark import load
from app.risk.history import sha256_file
from app.risk.intent_router import NaturalQuestion, PROMPT, keyword_route, preflight, proposal, route, validate_plan
from app.risk.mixed_sql import SourceAudit
from app.risk.source_merchant_pack import write_json
from app.service.store import digest
from scripts.verify_mixed import verify


def audit_stage(root, directory, *, code_root=None):
    contract, tasks = load(root, directory, code_root=code_root)
    groups, pairs, failures = {}, {}, []
    with SourceAudit(Path(contract["cohort_report"])) as source:
        for arm in contract["arms"]:
            for task in tasks:
                key = arm + "/" + task["split"]
                counts = groups.setdefault(key, Counter())
                counts["tasks"] += 1
                output = directory / arm / task["id"]
                path = output / "receipt.json"
                if not path.exists():
                    counts["missing_or_interrupted_slots"] += 1
                    failures.append({"id": task["id"], "arm": arm, "reason": "missing_or_interrupted_slot"})
                    continue
                receipt = json.loads(path.read_text(encoding="utf-8"))
                if receipt["task_hash"] != digest(task) or receipt["id"] != task["id"] or receipt["arm"] != arm: raise ValueError("Receipt binding differs")
                for name, expected in receipt["artifact_sha256"].items():
                    artifact = (output / name).resolve()
                    if not artifact.is_relative_to(output.resolve()) or sha256_file(artifact) != expected: raise ValueError("Receipt artifact changed")
                if receipt["status"] == "slot_failed":
                    counts["slot_failures"] += 1
                    failures.append({"id": task["id"], "arm": arm, "reason": receipt["error_type"]})
                    continue
                if not {"started.json", "route.json"} <= set(receipt["artifact_sha256"]): raise ValueError("Missing routing artifacts")
                result = json.loads((output / "route.json").read_text(encoding="utf-8"))
                if result["route"] != receipt["route"] or result["status"] != receipt["status"] or result["tool_attempts"] != 0: raise ValueError("Router receipt differs")
                if not 0 <= result["client_requests"] <= 2 or receipt["client_requests"] != result["client_requests"]: raise ValueError("Request ceiling differs")
                if arm == "keyword" and (result["route"] != keyword_route(NaturalQuestion(**task["question"])) or result["client_requests"] != 0): raise ValueError("Keyword replay differs")
                # Model only sees fixed prompt + question, never source/gold/financial observations.
                messages = result["messages"]
                if messages:
                    if messages[0] != {"role": "system", "content": PROMPT}: raise ValueError("Unexpected prompt")
                    if json.loads(messages[1]["content"]) != {"untrusted_question": task["question"]["text"]}: raise ValueError("Unexpected model input")
                    if any(m["role"] not in ("system", "user", "assistant") for m in messages): raise ValueError("Unexpected evidence message")
                if result["shared_guard"] != (preflight(NaturalQuestion(**task["question"])) is not None): raise ValueError("Shared guard differs")
                if arm == "model" and result["status"] == "routed" and not result["shared_guard"]:
                    answers = iter(m for m in messages if m["role"] == "assistant")
                    class Replay:
                        def chat(self, actual, tools=None):
                            # Full request prefix is deterministic, including repair instructions.
                            if tools != [] or actual != messages[:len(actual)]: raise ValueError("Changed replay request")
                            return next(answers)
                    replayed = route(Replay(), NaturalQuestion(**task["question"]))
                    if replayed != result: raise ValueError("Router transcript differs")
                routed = validate_plan(result["route"]) if result["route"] else None
                exact = bool(routed and {"decision": routed["decision"], "checks": routed["checks"]} == task["gold"])
                counts["exact_scope_matches"] += exact
                counts["shared_guard_tasks"] += result["shared_guard"]
                counts["client_requests"] += result["client_requests"]
                counts["format_repairs"] += result["format_repair_attempted"]
                counts["routing_failures"] += result["status"] != "routed"
                counts["false_execute_on_nonexecute"] += bool(routed and routed["decision"] == "execute" and task["gold"]["decision"] != "execute")
                counts["extra_checks"] += len(set(routed["checks"] if routed else []) - set(task["gold"]["checks"]))
                counts["missing_checks"] += len(set(task["gold"]["checks"]) - set(routed["checks"] if routed else []))
                pairs.setdefault(task["id"], {})[arm] = exact
                if not exact: failures.append({"id": task["id"], "arm": arm, "question": task["question"]["text"], "gold": task["gold"], "route": routed, "failure_code": result["failure_code"]})
                if routed and routed["decision"] == "execute":
                    expected = proposal(NaturalQuestion(**task["question"]), routed, case_key=digest([task["manifest_sha256"], task["facts_sha256"]]), runtime=digest(contract["code_file_sha256"]))
                    if json.loads((output / "plan.json").read_text(encoding="utf-8")) != expected: raise ValueError("Compiled plan differs")
                    if receipt["scope_confirmation"] != "SIMULATED_CONFIRMATION_NOT_HUMAN_REVIEW": raise ValueError("Simulation not declared")
                    proof = verify(output / "evidence/summary.json", source)
                    summary = json.loads((output / "evidence/summary.json").read_text(encoding="utf-8"))
                    if summary["question"]["checks"] != routed["checks"] or summary["question"]["min_records"] != 10: raise ValueError("Executed scope differs")
                    if not proof["task_passed"] or proof["extra_distinct_queries"] or proof["missing_required_queries"]: raise ValueError("Conditional financial verification failed")
                    if receipt["tool_attempts"] != summary["tool_attempts"]: raise ValueError("Tool count differs")
                    counts["conditional_sql_verified_reports"] += 1
                    counts["tool_attempts"] += receipt["tool_attempts"]
                elif receipt["tool_attempts"] != 0 or (output / "evidence").exists(): raise ValueError("Nonexecute queried facts")
                if (output / "usage.json").exists():
                    usage = json.loads((output / "usage.json").read_text(encoding="utf-8"))
                    counts["real_requests"] += usage["requests_sent"]
                    counts["unknown_cost_requests"] += usage["ledger"]["unknown_cost_requests"]
                    # Decimal sums are handled outside Counter for stable JSON.
                    counts["estimated_microyuan"] += int(Decimal(usage["ledger"]["run_committed_estimate_cny"]) * 1000000)
    comparison = {}
    for split in ("development", "evaluation"):
        pairs_split = [pairs.get(t["id"], {}) for t in tasks if t["split"] == split]
        comparison[split] = {"model_only_matches": sum(p.get("model", False) and not p.get("keyword", False) for p in pairs_split),
                             "keyword_only_matches": sum(p.get("keyword", False) and not p.get("model", False) for p in pairs_split),
                             "both_match": sum(p.get("keyword", False) and p.get("model", False) for p in pairs_split),
                             "neither_match": sum(not p.get("keyword", False) and not p.get("model", False) for p in pairs_split)}
    result = {"stage": "G6-A", "contract_sha256": sha256_file(directory / "contract.json"), "groups": groups,
              "paired": comparison, "failures": failures, "limits": contract["limits"], "labels_opened": False,
              "policy_approved": False, "deployed": False}
    write_json(directory / "audit.json", result)
    return result


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=root / "runs/natural-stage-v1")
    parser.add_argument("--frozen-code-root", type=Path)
    args = parser.parse_args()
    result = audit_stage(root, args.directory, code_root=args.frozen_code_root)
    print(json.dumps({k: v for k, v in result.items() if k != "failures"}, ensure_ascii=False, indent=2))


if __name__ == "__main__": main()
