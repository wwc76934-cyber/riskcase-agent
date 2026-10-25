"""Single-question planner and frozen v1/v2 comparison. Gold stays in host-only scoring."""

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import shutil
import time

from app.cli.mixed_investigation import execute
from app.llm.fake_client import FakeLLMClient
from app.risk import intent_router, planner_v2
from app.risk.history import sha256_file
from app.risk.intent_router import NaturalQuestion, validate_plan
from app.risk.mixed_investigation import MixedQuestion
from app.risk.mixed_sql import SourceAudit
from app.risk.source_merchant_pack import write_json
from app.service.store import digest
from app.tools.case_tools import strict_json
from scripts.verify_mixed import verify


METHODS = ("v1_keyword", "v1_model", "keyword", "model", "hybrid")
CODE = ["app/risk/planner_v2.py", "app/risk/intent_router.py", "app/cli/planner_v2.py",
        "app/risk/mixed_investigation.py", "app/risk/mixed_sql.py", "app/cli/mixed_investigation.py", "scripts/verify_mixed.py",
        "app/tools/case_tools.py", "app/risk/history.py", "app/risk/case_snapshot.py", "app/risk/merchant_research.py",
        "app/risk/merchant_stats.py", "app/risk/investigation.py", "app/risk/source_merchant_pack.py",
        "app/agent/react_agent.py", "app/llm/glm_client.py", "app/llm/fake_client.py", "app/llm/siliconflow_client.py",
        "app/llm/cost_ledger.py", "app/cli/investigate_live.py", "app/service/store.py", "config/siliconflow-live-v1.json"]


def dispatch(client, question, method):
    if method == "v1_keyword": return intent_router.route(None, question, method="keyword")
    if method == "v1_model": return intent_router.route(client, question, method="model")
    return planner_v2.route(client, question, method=method)


def live_client(root, directory):
    from app.cli.investigate_live import local_key
    from app.llm.cost_ledger import CostLedger
    from app.llm.siliconflow_client import SiliconFlowClient
    key = local_key()
    if not key: raise ValueError("Saved credential unavailable")
    policy = json.loads((root / "config/siliconflow-live-v1.json").read_text(encoding="utf-8"))
    run_id = "planner-v2-" + hashlib.sha256(str(directory.resolve()).encode("utf-8")).hexdigest()[:24]
    return SiliconFlowClient(key, policy=policy, ledger=CostLedger(root / "runs/llm-budget.sqlite"), run_id=run_id)


def single(root, directory, question, method, *, live=False):
    if method not in METHODS: raise ValueError("Unsupported method")
    if method not in ("keyword", "v1_keyword") and not live: raise ValueError("Model methods require explicit --live")
    directory.mkdir(parents=True, exist_ok=False)
    write_json(directory / "question.json", {"text": question.text})
    base = live_client(root, directory) if method not in ("keyword", "v1_keyword") else None
    try:
        result = dispatch(base, question, method)
        if base: result = base.scrub(result)
        write_json(directory / "route.json", result)
        return result
    finally:
        if base:
            base.close()
            write_json(directory / "usage.json", base.snapshot())


def prepare(root, directory, questions_file, *, methods=METHODS[:4], source_tasks=None, cohort_report=None):
    if not methods or len(set(methods)) != len(methods) or any(m not in METHODS for m in methods): raise ValueError("Distinct supported methods required")
    raw = json.loads(Path(questions_file).read_text(encoding="utf-8"))
    questions = raw["tasks"] if isinstance(raw, dict) else raw
    if not isinstance(questions, list) or not questions: raise ValueError("Explicit question set required")
    old_path = Path(source_tasks or root / "runs/mixed-stage-v1/tasks-host-only.json")
    old = json.loads(old_path.read_text(encoding="utf-8"))
    cases = list({t["manifest"]: t for t in old}.values())
    if not cases: raise ValueError("Cases required")
    tasks, identities = [], set()
    for index, item in enumerate(questions):
        identity = item["id"]
        if not isinstance(identity, str) or not re_safe_id(identity) or identity in identities: raise ValueError("Distinct safe question IDs required")
        identities.add(identity)
        question = NaturalQuestion(**item["question"])
        gold = item["gold"]
        if set(gold) != {"decision", "checks"}: raise ValueError("Gold schema differs")
        normalized = validate_plan({**gold, "reason_code": {"execute": "supported_scope", "clarify": "ambiguous_scope", "reject": "unsupported_scope"}[gold["decision"]]})
        gold = {"decision": normalized["decision"], "checks": normalized["checks"]}
        case = cases[index % len(cases)]
        manifest = Path(case["manifest"])
        if sha256_file(manifest) != case["manifest_sha256"] or sha256_file(manifest.parent / "facts.sqlite") != case["projection_sha256"]:
            raise ValueError("Frozen source case changed")
        tasks.append({"id": identity, "question": {"text": question.text}, "gold": gold,
                      "split": item.get("split", "acceptance"), "family": item.get("family", "unspecified"),
                      "manifest": str(manifest), "manifest_sha256": sha256_file(manifest), "facts_sha256": case["projection_sha256"]})
    directory.mkdir(parents=True, exist_ok=False)
    write_json(directory / "tasks-host-only.json", tasks)
    for name in CODE:
        target = directory / "frozen-code" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / name, target)
    cohort = Path(cohort_report or root / "data/cohorts/cohort-3bfdbaab486f/build-report.json")
    contract = {"version": "planner-v2-comparison-1", "tasks": len(tasks), "methods": list(methods), "max_client_requests": 2,
                "tasks_sha256": sha256_file(directory / "tasks-host-only.json"), "original_questions_sha256": sha256_file(questions_file),
                "source_tasks_sha256": sha256_file(old_path), "cohort_report": str(cohort), "cohort_sha256": sha256_file(cohort),
                "code_file_sha256": {name: sha256_file(root / name) for name in CODE},
                "v1_policy": intent_router.POLICY, "v2_policy": planner_v2.POLICY,
                "limits": ["Independent question authorship is recorded by the question-set owner; this runner cannot prove human blinding.",
                           "Cases reuse prior synthetic users. No new financial population or detection generalization is claimed.",
                           "v1 and v2 host guards differ; improvements cannot be attributed solely to a prompt.",
                           "All executable scopes are explicitly SIMULATED_CONFIRMATION_NOT_HUMAN_REVIEW, including wrong scopes.",
                           "Numeric verification is conditional on the routed scope; it cannot fix a semantic planning error.",
                           "No failed or started paid slot is retried. No financial approval, deployment or 2019 detector access."]}
    write_json(directory / "contract.json", contract)
    return contract


def re_safe_id(value):
    return 1 <= len(value) <= 100 and all(c.isascii() and (c.isalnum() or c in "-_") for c in value)


def load(root, directory, *, code_root=None):
    contract = json.loads((directory / "contract.json").read_text(encoding="utf-8"))
    if sha256_file(directory / "tasks-host-only.json") != contract["tasks_sha256"]: raise ValueError("Frozen tasks changed")
    for name, expected in contract["code_file_sha256"].items():
        if sha256_file((code_root or root) / name) != expected: raise ValueError("Frozen code changed: " + name)
    if sha256_file(Path(contract["cohort_report"])) != contract["cohort_sha256"]: raise ValueError("Source report changed")
    tasks = json.loads((directory / "tasks-host-only.json").read_text(encoding="utf-8"))
    if len(tasks) != contract["tasks"] or len({t["id"] for t in tasks}) != len(tasks): raise ValueError("Frozen task count differs")
    for task in tasks:
        path = Path(task["manifest"])
        if sha256_file(path) != task["manifest_sha256"] or sha256_file(path.parent / "facts.sqlite") != task["facts_sha256"]: raise ValueError("Source case changed")
    return contract, tasks


def run_slot(root, directory, task, method, *, live=False):
    output = directory / method / task["id"]
    try: output.mkdir(parents=True, exist_ok=False)
    except FileExistsError: return {"id": task["id"], "method": method, "status": "existing_slot_not_retried"}
    started, base = time.monotonic(), None
    write_json(output / "started.json", {"task_hash": digest(task), "method": method, "live": live})
    try:
        if method not in ("keyword", "v1_keyword"):
            if not live: raise ValueError("Explicit --live required")
            base = live_client(root, output)
        # Only the question enters the model. Gold/case/source data remain host-only.
        result = dispatch(base, NaturalQuestion(**task["question"]), method)
        if base: result = base.scrub(result)
        write_json(output / "route.json", result)
        financial = None
        if result["route"] and result["route"]["decision"] == "execute":
            proposer = intent_router.proposal if method.startswith("v1_") else planner_v2.proposal
            proposed = proposer(NaturalQuestion(**task["question"]), result["route"], case_key=digest([task["manifest_sha256"], task["facts_sha256"]]), runtime=digest(json.loads((directory / "contract.json").read_text(encoding="utf-8"))["code_file_sha256"]))
            write_json(output / "plan.json", proposed)
            financial = execute(root, output / "evidence", Path(task["manifest"]), MixedQuestion(result["route"]["checks"]), arm="fixed")
        receipt = {"status": result["status"], "id": task["id"], "method": method, "task_hash": digest(task),
                   "route": result["route"], "client_requests": result["client_requests"],
                   "tool_attempts": financial["tool_attempts"] if financial else 0,
                   "scope_confirmation": "SIMULATED_CONFIRMATION_NOT_HUMAN_REVIEW" if financial else None}
    except Exception as exc:
        receipt = {"status": "slot_failed", "id": task["id"], "method": method, "task_hash": digest(task), "error_type": type(exc).__name__}
    finally:
        if base:
            base.close()
            write_json(output / "usage.json", base.snapshot())
    receipt["elapsed_seconds"] = round(time.monotonic() - started, 3)
    receipt["artifact_sha256"] = {str(p.relative_to(output)).replace("\\", "/"): sha256_file(p) for p in output.rglob("*") if p.is_file() and p.name != "verification.json"}
    write_json(output / "receipt.json", receipt)
    return {"id": task["id"], "method": method, "status": receipt["status"]}


def _scope(body): return {"decision": body["decision"], "checks": body["checks"]} if body else None


def _first_route(result, method):
    if result["shared_guard"] or method in ("keyword", "v1_keyword"): return result["route"]
    if not method.startswith("v1_"): return (result["first_attempt"] or {}).get("route")
    messages = [m for m in result["messages"] if m["role"] == "assistant"]
    if not messages: return None
    text = messages[0].get("content")
    try:
        if isinstance(text, str) and text.startswith("```json\n") and text.endswith("\n```"): text = text[8:-4]
        return validate_plan(strict_json(text))
    except (ValueError, TypeError): return None


def audit(root, directory, *, code_root=None):
    contract, tasks = load(root, directory, code_root=code_root)
    groups, failures, paired = {}, [], {}
    with SourceAudit(Path(contract["cohort_report"])) as source:
        for method in contract["methods"]:
            for task in tasks:
                counts = groups.setdefault(method + "/" + task["split"], Counter())
                counts["tasks"] += 1
                output = directory / method / task["id"]
                path = output / "receipt.json"
                if not path.exists():
                    counts["missing_or_interrupted_slots"] += 1
                    continue
                receipt = json.loads(path.read_text(encoding="utf-8"))
                if receipt["task_hash"] != digest(task) or receipt["method"] != method or receipt["id"] != task["id"]: raise ValueError("Receipt task binding differs")
                for name, expected in receipt["artifact_sha256"].items():
                    artifact = (output / name).resolve()
                    if not artifact.is_relative_to(output.resolve()) or sha256_file(artifact) != expected: raise ValueError("Receipt artifact changed")
                if receipt["status"] == "slot_failed":
                    counts["slot_failures"] += 1
                    failures.append({"id": task["id"], "method": method, "error_type": receipt["error_type"]})
                    continue
                if not {"started.json", "route.json"} <= set(receipt["artifact_sha256"]): raise ValueError("Missing route artifacts")
                result = json.loads((output / "route.json").read_text(encoding="utf-8"))
                if result["route"] != receipt["route"] or result["status"] != receipt["status"] or result["tool_attempts"] != 0: raise ValueError("Route receipt differs")
                if not 0 <= result["client_requests"] <= 2 or receipt["client_requests"] != result["client_requests"]: raise ValueError("Request budget differs")
                if result["status"] == "routed":
                    raw = result.get("response_records") if not method.startswith("v1_") else [m for m in result["messages"] if m["role"] == "assistant"]
                    replay = dispatch(FakeLLMClient(raw or []), NaturalQuestion(**task["question"]), method)
                    if replay != result: raise ValueError("Deterministic route replay differs")
                routed = validate_plan(result["route"]) if result["route"] else None
                exact = _scope(routed) == task["gold"]
                counts["exact_scope_matches"] += exact
                counts["first_scope_matches"] += _scope(_first_route(result, method)) == task["gold"]
                counts["shared_guard_tasks"] += result["shared_guard"]
                counts["client_requests"] += result["client_requests"]
                counts["routing_failures"] += result["status"] != "routed"
                counts["format_corrections"] += result["format_repair_attempted"]
                counts["contract_corrections"] += result.get("contract_repair_attempted", False)
                counts["false_execute_on_nonexecute"] += bool(routed and routed["decision"] == "execute" and task["gold"]["decision"] != "execute")
                counts["extra_checks"] += len(set(routed["checks"] if routed else []) - set(task["gold"]["checks"]))
                counts["missing_checks"] += len(set(task["gold"]["checks"]) - set(routed["checks"] if routed else []))
                paired.setdefault(task["id"], {})[method] = exact
                if not exact: failures.append({"id": task["id"], "method": method, "family": task["family"], "gold": task["gold"], "route": routed, "failure_code": result["failure_code"]})
                if routed and routed["decision"] == "execute":
                    proposer = intent_router.proposal if method.startswith("v1_") else planner_v2.proposal
                    expected = proposer(NaturalQuestion(**task["question"]), routed, case_key=digest([task["manifest_sha256"], task["facts_sha256"]]), runtime=digest(contract["code_file_sha256"]))
                    if json.loads((output / "plan.json").read_text(encoding="utf-8")) != expected: raise ValueError("Compiled plan differs")
                    if receipt["scope_confirmation"] != "SIMULATED_CONFIRMATION_NOT_HUMAN_REVIEW": raise ValueError("Scope simulation not disclosed")
                    proof = verify(output / "evidence/summary.json", source)
                    summary = json.loads((output / "evidence/summary.json").read_text(encoding="utf-8"))
                    if summary["question"]["checks"] != routed["checks"] or summary["question"]["min_records"] != 10: raise ValueError("Executed scope differs")
                    if not proof["task_passed"] or proof["extra_distinct_queries"] or proof["missing_required_queries"]: raise ValueError("Financial SQL proof differs")
                    counts["conditional_sql_verified_reports"] += 1
                    counts["tool_attempts"] += summary["tool_attempts"]
                elif receipt["tool_attempts"] != 0 or (output / "evidence").exists(): raise ValueError("Nonexecute route investigated")
                if (output / "usage.json").exists():
                    usage = json.loads((output / "usage.json").read_text(encoding="utf-8"))
                    counts["real_requests"] += usage["requests_sent"]
                    counts["unknown_cost_requests"] += usage["ledger"]["unknown_cost_requests"]
                    counts["estimated_microyuan"] += int(Decimal(usage["ledger"]["run_committed_estimate_cny"]) * 1000000)
    result = {"contract_sha256": sha256_file(directory / "contract.json"), "groups": groups, "paired": paired, "failures": failures,
              "limits": contract["limits"], "labels_opened": False, "policy_approved": False, "deployed": False}
    write_json(directory / "audit.json", result)
    return result


def main():
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("single", "prepare", "run", "audit"))
    parser.add_argument("--text")
    parser.add_argument("--method", choices=METHODS, default="keyword")
    parser.add_argument("--methods", nargs="+", choices=METHODS, default=list(METHODS[:4]))
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--questions", type=Path)
    parser.add_argument("--workers", type=int, choices=(1, 2), default=1)
    parser.add_argument("--frozen-code-root", type=Path)
    args = parser.parse_args()
    if args.action == "single":
        result = single(root, args.directory, NaturalQuestion(args.text), args.method, live=args.live)
        print(json.dumps({k: v for k, v in result.items() if k not in ("messages", "response_records")}, ensure_ascii=False, indent=2)); return
    if args.action == "prepare":
        if not args.questions: parser.error("--questions required")
        result = prepare(root, args.directory, args.questions, methods=args.methods)
        print(json.dumps(result, ensure_ascii=False)); return
    if args.action == "audit":
        result = audit(root, args.directory, code_root=args.frozen_code_root)
        print(json.dumps({k: result[k] for k in ("contract_sha256", "groups", "limits")}, ensure_ascii=False, indent=2)); return
    contract, tasks = load(root, args.directory)
    if args.method not in contract["methods"]: parser.error("Method not frozen")
    if args.method not in ("keyword", "v1_keyword") and not args.live: parser.error("--live required for model methods")
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(run_slot, root, args.directory, t, args.method, live=args.live) for t in tasks]
        for future in futures: print(json.dumps(future.result()), flush=True)


if __name__ == "__main__": main()
