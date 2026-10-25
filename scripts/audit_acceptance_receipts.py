"""Independently score existing paid receipts; no clients, model calls, replay or reruns."""

import argparse
from collections import Counter
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import unicodedata

from scripts.score_acceptance_intent import load_tasks, score, sha256, strict_load


METHODS = ("v1_keyword", "v1_model", "keyword", "model")


def digest(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def first_route(result, method):
    if result.get("shared_guard") or method.endswith("keyword"):
        return result.get("route")
    if method == "model":
        return (result.get("first_attempt") or {}).get("route")
    # Historical client has no separate first-attempt field. Read its stored
    # first response only; never import or invoke either router implementation.
    answers = [m for m in result.get("messages", []) if m.get("role") == "assistant"]
    if not answers:
        return None
    raw = answers[0].get("content")
    if not isinstance(raw, str):
        return None
    if raw.startswith("```json\n") and raw.endswith("\n```"):
        raw = raw[8:-4]
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def audit(root, directory, output):
    root, directory, output = Path(root), Path(directory), Path(output)
    original_contract, dataset = load_tasks(root / "config/acceptance-intent-v2-host-only.json")
    contract = strict_load(directory / "contract.json")
    tasks = strict_load(directory / "tasks-host-only.json")
    previous = strict_load(directory / "audit-summary.json")
    previous_full = strict_load(directory / "audit.json")
    if (contract["methods"] != list(METHODS) or contract["tasks"] != 100
            or contract["original_questions_sha256"] != original_contract["dataset_sha256"]
            or contract["tasks_sha256"] != sha256(directory / "tasks-host-only.json")
            or previous["contract_sha256"] != sha256(directory / "contract.json")
            or previous_full["contract_sha256"] != sha256(directory / "contract.json")):
        raise ValueError("frozen_acceptance_run_binding_differs")
    source = {task["id"]: task for task in dataset["tasks"]}
    if len(tasks) != 100 or {t["id"] for t in tasks} != set(source):
        raise ValueError("complete_original_acceptance_tasks_required")
    normalized_texts = 0
    for task in tasks:
        gold = source[task["id"]]
        if (task["question"]["text"] != unicodedata.normalize("NFKC", gold["question"]["text"]).strip() or task["gold"] != gold["gold"]
                or task["family"] != gold["family"] or task["split"] != "acceptance"):
            raise ValueError("original_acceptance_text_or_gold_changed")
        normalized_texts += task["question"]["text"] != gold["question"]["text"]
    for name, expected in contract["code_file_sha256"].items():
        frozen = (directory / "frozen-code" / name).resolve()
        if not frozen.is_relative_to((directory / "frozen-code").resolve()) or sha256(frozen) != expected:
            raise ValueError("frozen_code_snapshot_changed")
    if previous_full["labels_opened"] or previous_full["policy_approved"] or previous_full["deployed"]:
        raise ValueError("unapproved_no_label_acceptance_required")

    output.mkdir(parents=True, exist_ok=False)
    results, receipt_hashes, reservations = {}, {}, set()
    for method in METHODS:
        method_directory = directory / method
        if {p.name for p in method_directory.iterdir() if p.is_dir()} != set(source):
            raise ValueError("method_slots_incomplete_or_extra")
        records, initial = [], []
        counts = Counter(slots=0, receipts=0, conditional_sql_verified_reports=0, client_requests=0,
                         real_requests=0, unknown_cost_requests=0, estimated_microyuan=0, tool_attempts=0,
                         shared_guard_tasks=0, format_corrections=0, contract_corrections=0, routing_failures=0)
        for task in tasks:
            identity = task["id"]
            slot = method_directory / identity
            receipt_path = slot / "receipt.json"
            counts["slots"] += 1
            if not receipt_path.is_file():
                # Missing remains an explicit null in the 100-task denominator.
                records.append({"id": identity, "route": None, "status": "missing_receipt"})
                initial.append({"id": identity, "route": None, "status": "missing_receipt"})
                continue
            receipt = strict_load(receipt_path)
            counts["receipts"] += 1
            receipt_hashes[f"{method}/{identity}/receipt.json"] = sha256(receipt_path)
            if receipt["id"] != identity or receipt["method"] != method or receipt["task_hash"] != digest(task):
                raise ValueError("receipt_task_binding_differs")
            for name, expected in receipt["artifact_sha256"].items():
                artifact = (slot / name).resolve()
                if not artifact.is_relative_to(slot.resolve()) or sha256(artifact) != expected:
                    raise ValueError("receipt_artifact_binding_differs")
            record = {"id": identity, "route": receipt.get("route"), "status": receipt["status"],
                      "receipt_sha256": sha256(receipt_path), "client_requests": receipt.get("client_requests", 0)}
            records.append(record)
            if receipt["status"] == "slot_failed":
                initial.append({"id": identity, "route": None, "status": "slot_failed"})
                counts["routing_failures"] += 1
                continue
            if not {"started.json", "route.json"} <= set(receipt["artifact_sha256"]):
                raise ValueError("required_route_artifacts_missing")
            started = strict_load(slot / "started.json")
            result = strict_load(slot / "route.json")
            if (started["task_hash"] != digest(task) or started["method"] != method
                    or result["route"] != receipt["route"] or result["status"] != receipt["status"]
                    or result["client_requests"] != receipt["client_requests"] or result["tool_attempts"] != 0
                    or not 0 <= result["client_requests"] <= contract["max_client_requests"]):
                raise ValueError("route_receipt_or_budget_differs")
            initial.append({"id": identity, "route": first_route(result, method), "status": result["status"]})
            counts["client_requests"] += result["client_requests"]
            counts["routing_failures"] += result["status"] != "routed"
            counts["shared_guard_tasks"] += bool(result["shared_guard"])
            counts["format_corrections"] += bool(result["format_repair_attempted"])
            counts["contract_corrections"] += bool(result.get("contract_repair_attempted", False))
            routed = receipt.get("route")
            if routed and routed["decision"] == "execute":
                summary_path, proof_path = slot / "evidence/summary.json", slot / "evidence/verification.json"
                summary, proof = strict_load(summary_path), strict_load(proof_path)
                if (proof["status"] != "source_sql_verified" or proof["summary_sha256"] != sha256(summary_path)
                        or not proof["task_passed"] or not proof["answer_published"] or proof["labels_opened"]
                        or proof["extra_distinct_queries"] or proof["missing_required_queries"]
                        or summary["question"] != {"checks": routed["checks"], "min_records": 10}
                        or summary["policy_approved"] or summary["deployed"] or summary["real_LLM_requests"] != 0
                        or summary["status"] != "completed_validated"
                        or receipt["scope_confirmation"] != "SIMULATED_CONFIRMATION_NOT_HUMAN_REVIEW"
                        or receipt["tool_attempts"] != summary["tool_attempts"]):
                    raise ValueError("conditional_sql_proof_or_scope_binding_differs")
                for name, expected in summary["artifact_sha256"].items():
                    artifact = (summary_path.parent / name).resolve()
                    if not artifact.is_relative_to(summary_path.parent.resolve()) or sha256(artifact) != expected:
                        raise ValueError("conditional_sql_artifact_changed")
                counts["conditional_sql_verified_reports"] += 1
                counts["tool_attempts"] += summary["tool_attempts"]
                receipt_hashes[f"{method}/{identity}/evidence/verification.json"] = sha256(proof_path)
            elif receipt["tool_attempts"] != 0 or (slot / "evidence").exists():
                raise ValueError("nonexecute_has_financial_report")
            usage_path = slot / "usage.json"
            if method.endswith("model"):
                if "usage.json" not in receipt["artifact_sha256"]:
                    raise ValueError("model_usage_receipt_required")
                usage = strict_load(usage_path)
                ledger = usage["ledger"]
                if (usage["requests_sent"] != result["client_requests"]
                        or len(usage["request_audit"]) != usage["requests_sent"]
                        or len(ledger["entries"]) != usage["requests_sent"]):
                    raise ValueError("request_and_usage_count_differs")
                request_ids = [item["reservation_id"] for item in usage["request_audit"]]
                entry_ids = [item["reservation_id"] for item in ledger["entries"]]
                if len(set(request_ids)) != len(request_ids) or set(request_ids) != set(entry_ids) or reservations & set(request_ids):
                    raise ValueError("duplicate_or_unbound_paid_request")
                reservations.update(request_ids)
                counts["real_requests"] += usage["requests_sent"]
                counts["unknown_cost_requests"] += ledger["unknown_cost_requests"]
                counts["estimated_microyuan"] += int(Decimal(ledger["run_committed_estimate_cny"]) * 1_000_000)
            elif usage_path.exists() or result["client_requests"] != 0:
                raise ValueError("keyword_method_has_paid_request")
        scored, first = score(dataset, records), score(dataset, initial)
        group = previous["groups"][method + "/acceptance"]
        if group != previous_full["groups"][method + "/acceptance"]:
            raise ValueError("prior_audit_outputs_disagree")
        comparable = {"tasks": scored["denominator"], "exact_scope_matches": scored["aggregate"].get("exact_matches", 0),
                      "first_scope_matches": first["aggregate"].get("exact_matches", 0),
                      **{key: scored["aggregate"].get(key, 0) for key in ("false_execute_on_nonexecute", "extra_checks", "missing_checks")},
                      **{key: counts[key] for key in ("client_requests", "routing_failures", "shared_guard_tasks", "format_corrections",
                                                    "contract_corrections", "conditional_sql_verified_reports", "tool_attempts")}}
        if method.endswith("model"):
            comparable.update({key: counts[key] for key in ("real_requests", "unknown_cost_requests", "estimated_microyuan")})
        if comparable != group:
            raise ValueError("independent_scoring_or_receipt_counts_disagree")
        records_path = output / f"{method}-records.json"
        records_path.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        scored.update(dataset_sha256=original_contract["dataset_sha256"], results_sha256=sha256(records_path),
                      scorer_sha256=sha256(root / "scripts/score_acceptance_intent.py"))
        (output / f"{method}-score.json").write_text(json.dumps(scored, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        results[method] = {**comparable, **dict(counts), "exact_matches": scored["aggregate"].get("exact_matches", 0),
                           "unavailable_routes": scored["aggregate"].get("unavailable_routes", 0),
                           "overabstention_on_execute": scored["aggregate"].get("overabstention_on_execute", 0),
                           "invalid_routes": scored["aggregate"].get("invalid_routes", 0),
                           "records_sha256": sha256(records_path), "score_sha256": sha256(output / f"{method}-score.json")}
    result = {"status": "independent_receipt_scores_and_counts_verified", "dataset_sha256": original_contract["dataset_sha256"],
              "run_contract_sha256": sha256(directory / "contract.json"), "prior_audit_sha256": sha256(directory / "audit.json"),
              "prior_summary_sha256": sha256(directory / "audit-summary.json"), "script_sha256": sha256(Path(__file__)),
              "scorer_sha256": sha256(root / "scripts/score_acceptance_intent.py"), "groups": results,
              "slots": sum(g["slots"] for g in results.values()), "receipts": sum(g["receipts"] for g in results.values()),
              "conditional_sql_reports": sum(g["conditional_sql_verified_reports"] for g in results.values()),
              "real_requests": sum(g["real_requests"] for g in results.values()),
              "estimated_microyuan": sum(g["estimated_microyuan"] for g in results.values()),
              "input_text_normalization": "NFKC plus strip; every runtime question checked against the original frozen text",
              "normalized_texts": normalized_texts,
              "all_prior_counts_match": True, "new_model_requests": 0, "paid_slots_retried": 0,
              "receipt_and_proof_sha256": receipt_hashes,
              "limits": ["Independent scoring uses the original frozen helper-authored gold, not expert or real-user labels.",
                         "Conditional SQL proofs were checked for hashes and scope binding; this script does not rerun financial SQL.",
                         "SQL correctness on a routed scope cannot correct a wrong natural-language scope.",
                         "v1 and v2 host guards differ; no prompt-only causal attribution.",
                         "All execute scopes were simulated confirmations, including wrongly routed scopes; no actual financial approval.",
                         "Full-repository tests and remote CI are separate evidence, not established by this audit."]}
    (output / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=root / "runs/planner-v2-acceptance")
    parser.add_argument("--output", type=Path, default=root / "runs/planner-v2-independent-scoring")
    args = parser.parse_args()
    result = audit(root, args.directory, args.output)
    print(json.dumps({key: result[key] for key in ("status", "slots", "receipts", "conditional_sql_reports", "real_requests", "estimated_microyuan", "all_prior_counts_match")}, indent=2))


if __name__ == "__main__":
    main()
