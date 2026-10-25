"""Host-only independent intent scoring, with missing and invalid runs kept as failures."""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path


CHECKS = ("frequency", "amount", "merchant", "count_share", "amount_share")
REASONS = {"execute": {"supported_scope"}, "clarify": {"ambiguous_scope"},
           "reject": {"unsupported_scope", "restricted_request", "unsupported_window"}}


def strict_load(path):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result: raise ValueError("duplicate_json_key")
            result[key] = value
        return result
    def constant(_): raise ValueError("nonfinite_json")
    return json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=pairs, parse_constant=constant)


def sha256(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_tasks(dataset_path, contract_path=None):
    dataset_path = Path(dataset_path)
    contract_path = Path(contract_path) if contract_path else dataset_path.with_name("acceptance-intent-v2-contract.json")
    contract = strict_load(contract_path)
    if sha256(dataset_path) != contract["dataset_sha256"]: raise ValueError("frozen_acceptance_dataset_changed")
    dataset = strict_load(dataset_path)
    tasks = dataset["tasks"]
    counts = Counter(task["gold"]["decision"] for task in tasks)
    if len(tasks) != 100 or len({task["id"] for task in tasks}) != 100 or counts != {"execute": 60, "clarify": 20, "reject": 20}:
        raise ValueError("acceptance_composition_changed")
    for task in tasks:
        if task["gold"]["decision"] not in REASONS or not isinstance(task["gold"]["checks"], list): raise ValueError("invalid_gold")
        checks = task["gold"]["checks"]
        if checks != [c for c in CHECKS if c in checks] or len(set(checks)) != len(checks): raise ValueError("invalid_gold_checks")
        if bool(checks) != (task["gold"]["decision"] == "execute"): raise ValueError("gold_scope_decision_mismatch")
    return contract, dataset


def normalized_route(route):
    if not isinstance(route, dict) or set(route) != {"decision", "checks", "reason_code"}: raise ValueError("invalid_route_fields")
    decision, checks, reason = route["decision"], route["checks"], route["reason_code"]
    if type(decision) is not str or decision not in REASONS or type(reason) is not str or reason not in REASONS[decision]: raise ValueError("invalid_route_decision_or_reason")
    if not isinstance(checks, list) or any(type(c) is not str or c not in CHECKS for c in checks) or len(set(checks)) != len(checks): raise ValueError("invalid_route_checks")
    if bool(checks) != (decision == "execute"): raise ValueError("route_scope_decision_mismatch")
    return {"decision": decision, "checks": [c for c in CHECKS if c in checks]}


def score(dataset, records):
    """Each record: id, route (strict three-field JSON or null), optional status/diagnostics."""
    tasks = {task["id"]: task for task in dataset["tasks"]}
    indexed = {}
    for record in records:
        if not isinstance(record, dict) or type(record.get("id")) is not str: raise ValueError("record_id_required")
        if record["id"] not in tasks: raise ValueError("unknown_acceptance_id")
        if record["id"] in indexed: raise ValueError("duplicate_acceptance_id")
        indexed[record["id"]] = record
    total, families, errors = Counter(), {}, []
    for identity, task in tasks.items():
        counts = Counter(tasks=1)
        golden = task["gold"]
        counts["gold_" + golden["decision"]] = 1
        record = indexed.get(identity)
        routed, invalid = None, None
        if record is None:
            counts["missing_records"] = 1
            invalid = "missing_record"
        elif record.get("route") is None:
            counts["unavailable_routes"] = 1
            invalid = record.get("status", "route_unavailable")
        else:
            try: routed = normalized_route(record["route"])
            except ValueError as exc:
                counts["invalid_routes"] = 1
                invalid = str(exc)
        exact = routed == golden
        counts["exact_matches"] = int(exact)
        if routed:
            counts["decision_matches"] = int(routed["decision"] == golden["decision"])
            counts["predicted_" + routed["decision"]] = 1
            counts["false_execute_on_nonexecute"] = int(routed["decision"] == "execute" and golden["decision"] != "execute")
            counts["overabstention_on_execute"] = int(routed["decision"] != "execute" and golden["decision"] == "execute")
            counts["extra_checks"] = len(set(routed["checks"]) - set(golden["checks"]))
            counts["missing_checks"] = len(set(golden["checks"]) - set(routed["checks"]))
            counts["overscoped_execute_requests"] = int(routed["decision"] == "execute" and bool(set(routed["checks"]) - set(golden["checks"])))
        else:
            counts["missing_checks"] = len(golden["checks"])
        total.update(counts)
        families.setdefault(task["family"], Counter()).update(counts)
        if not exact:
            errors.append({"id": identity, "family": task["family"], "question": task["question"], "gold": golden,
                           "route": routed, "invalid_or_unavailable": invalid, "gold_reason": task["gold_reason"]})
    result = {"dataset_id": dataset["dataset_id"], "aggregate": dict(total), "by_family": {k: dict(v) for k, v in families.items()},
              "errors": errors, "denominator": len(tasks),
              "exact_match_rate": total["exact_matches"] / len(tasks),
              "limits": dataset["limitations"], "scoring": dataset["policy"]["scoring"]}
    return result


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=root / "config/acceptance-intent-v2-host-only.json")
    parser.add_argument("--contract", type=Path)
    parser.add_argument("--results", required=True, type=Path, help="JSON list, or object containing results list; each record has id and route")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    contract, dataset = load_tasks(args.dataset, args.contract)
    records = strict_load(args.results)
    if isinstance(records, dict): records = records["results"]
    if not isinstance(records, list): raise ValueError("results_list_required")
    result = score(dataset, records)
    result.update(dataset_sha256=contract["dataset_sha256"], results_sha256=sha256(args.results),
                  scorer_sha256=sha256(__file__))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("dataset_id", "dataset_sha256", "aggregate", "exact_match_rate", "denominator")}, ensure_ascii=False, indent=2))


if __name__ == "__main__": main()
