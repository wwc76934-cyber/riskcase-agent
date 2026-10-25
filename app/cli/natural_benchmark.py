"""Frozen author-curated intent benchmark; simulated scope acceptance is never human review."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import shutil
import time

from app.cli.mixed_investigation import execute
from app.risk.history import sha256_file
from app.risk.intent_router import NaturalQuestion, POLICY, PROMPT, proposal, route
from app.risk.mixed_investigation import MixedQuestion
from app.risk.source_merchant_pack import write_json
from app.service.store import digest


CODE = ["app/risk/intent_router.py", "app/cli/natural_benchmark.py", "scripts/verify_natural.py",
        "app/risk/mixed_investigation.py", "app/risk/mixed_sql.py", "app/cli/mixed_investigation.py",
        "scripts/verify_mixed.py", "app/tools/case_tools.py", "app/risk/investigation.py",
        "app/risk/merchant_research.py", "app/risk/merchant_stats.py", "app/risk/history.py",
        "app/llm/glm_client.py", "app/llm/siliconflow_client.py", "app/llm/cost_ledger.py",
        "app/risk/case_snapshot.py", "app/agent/react_agent.py", "app/llm/fake_client.py",
        "app/risk/source_merchant_pack.py", "app/cli/investigate_live.py", "app/service/store.py",
        "config/natural-questions-v1.json", "config/siliconflow-live-v1.json"]


def prepare(root, directory):
    questions_path = root / "config/natural-questions-v1.json"
    source = json.loads(questions_path.read_text(encoding="utf-8"))
    old = json.loads((root / "runs/mixed-stage-v1/tasks-host-only.json").read_text(encoding="utf-8"))
    cases = {}
    for split in ("development", "evaluation"):
        cases[split] = list({t["manifest"]: t for t in old if t["split"] == split}.values())
        if len(cases[split]) != 4 or len(source[split]) != 40: raise ValueError("Declared 4-case / 40-question split required")
    directory.mkdir(parents=True, exist_ok=False)
    tasks = []
    for split in ("development", "evaluation"):
        for i, (family, text, decision, checks) in enumerate(source[split]):
            case = cases[split][i % 4]
            manifest = Path(case["manifest"])
            if sha256_file(manifest) != case["manifest_sha256"] or sha256_file(manifest.parent / "facts.sqlite") != case["projection_sha256"]:
                raise ValueError("Previous frozen case changed")
            tasks.append({"id": f"{split}-{i:02d}", "split": split, "family": family,
                          "question": asdict(NaturalQuestion(text)), "gold": {"decision": decision, "checks": list(MixedQuestion(checks).checks) if checks else []},
                          "manifest": str(manifest), "manifest_sha256": sha256_file(manifest), "facts_sha256": case["projection_sha256"], "user_id": case["user_id"]})
    write_json(directory / "tasks-host-only.json", tasks)
    for name in CODE:
        dest = directory / "frozen-code" / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / name, dest)
    contract = {"stage": "G6-A", "tasks": 80, "arms": ["keyword", "model"], "max_router_requests": 2,
                "tasks_sha256": sha256_file(directory / "tasks-host-only.json"), "prompt_sha256": digest(PROMPT), "policy": POLICY,
                "code_file_sha256": {name: sha256_file(root / name) for name in CODE},
                "cohort_report": str(root / "data/cohorts/cohort-3bfdbaab486f/build-report.json"),
                "cohort_sha256": sha256_file(root / "data/cohorts/cohort-3bfdbaab486f/build-report.json"),
                "limits": ["Author-curated questions, no independent annotator or genuine analyst traffic.",
                           "Both splits share intent families; no claim of semantic distribution independence.",
                           "Eight reused synthetic cases; no new financial users or detector holdout.",
                           "Every valid execute route is SIMULATED_CONFIRMATION_NOT_HUMAN_REVIEW, including wrong scopes.",
                           "Numeric SQL validation is conditional on routed scope, not proof that scope matches the user.",
                           "No retries of failed/started slots, no prompt/lexicon changes after paid start.",
                           "Same narrow host guard in both arms; guard successes are not model-only successes.",
                           "No financial approval/deployment; 2019 detector test remains sealed."]}
    write_json(directory / "contract.json", contract)
    return contract


def load(root, directory, *, code_root=None):
    contract = json.loads((directory / "contract.json").read_text(encoding="utf-8"))
    if sha256_file(directory / "tasks-host-only.json") != contract["tasks_sha256"]: raise ValueError("Frozen tasks changed")
    for name, expected in contract["code_file_sha256"].items():
        if sha256_file((code_root or root) / name) != expected: raise ValueError("Frozen code changed: " + name)
    if sha256_file(Path(contract["cohort_report"])) != contract["cohort_sha256"]: raise ValueError("Source report changed")
    tasks = json.loads((directory / "tasks-host-only.json").read_text(encoding="utf-8"))
    if len(tasks) != 80 or len({t["id"] for t in tasks}) != 80: raise ValueError("Task count/IDs changed")
    users = {s: {t["user_id"] for t in tasks if t["split"] == s} for s in ("development", "evaluation")}
    if len(users["development"]) != 4 or len(users["evaluation"]) != 4 or users["development"] & users["evaluation"]: raise ValueError("Case split leakage")
    for task in tasks:
        manifest = Path(task["manifest"])
        if sha256_file(manifest) != task["manifest_sha256"] or sha256_file(manifest.parent / "facts.sqlite") != task["facts_sha256"]: raise ValueError("Case changed")
    return contract, tasks


def slot(root, directory, task, arm, live):
    output = directory / arm / task["id"]
    try: output.mkdir(parents=True, exist_ok=False)
    except FileExistsError: return {"id": task["id"], "arm": arm, "status": "existing_slot_not_retried"}
    start, client = time.monotonic(), None
    write_json(output / "started.json", {"id": task["id"], "arm": arm, "task_hash": digest(task), "live": live})
    try:
        if arm == "model":
            if not live: raise ValueError("Model arm requires explicit --live; keyword is the offline control")
            from app.cli.investigate_live import local_key
            from app.llm.cost_ledger import CostLedger
            from app.llm.siliconflow_client import SiliconFlowClient
            key = local_key()
            if not key: raise ValueError("Saved credential unavailable")
            policy = json.loads((root / "config/siliconflow-live-v1.json").read_text(encoding="utf-8"))
            run_id = "natural-" + hashlib.sha256(str(output.resolve()).encode("utf-8")).hexdigest()[:24]
            client = SiliconFlowClient(key, policy=policy, ledger=CostLedger(root / "runs/llm-budget.sqlite"), run_id=run_id)
        question = NaturalQuestion(**task["question"])
        result = route(client, question, method=arm)
        if client: result = client.scrub(result)
        write_json(output / "route.json", result)
        financial = None
        if result["route"] and result["route"]["decision"] == "execute":
            proposed = proposal(question, result["route"], case_key=digest([task["manifest_sha256"], task["facts_sha256"]]), runtime=digest(json.loads((directory / "contract.json").read_text(encoding="utf-8"))["code_file_sha256"]))
            write_json(output / "plan.json", proposed)
            # Deliberately no gold check here: measure the harm of wrong executable scopes too.
            financial = execute(root, output / "evidence", Path(task["manifest"]), MixedQuestion(result["route"]["checks"]), arm="fixed")
        receipt = {"status": result["status"], "id": task["id"], "arm": arm, "task_hash": digest(task),
                   "route": result["route"], "client_requests": result["client_requests"],
                   "tool_attempts": financial["tool_attempts"] if financial else 0,
                   "scope_confirmation": "SIMULATED_CONFIRMATION_NOT_HUMAN_REVIEW" if financial else None,
                   "financial_status": financial["status"] if financial else None}
    except Exception as exc:
        receipt = {"status": "slot_failed", "id": task["id"], "arm": arm, "task_hash": digest(task), "error_type": type(exc).__name__}
    finally:
        if client:
            client.close()
            write_json(output / "usage.json", client.snapshot())
    receipt["elapsed_seconds"] = round(time.monotonic() - start, 3)
    receipt["artifact_sha256"] = {str(p.relative_to(output)).replace("\\", "/"): sha256_file(p) for p in output.rglob("*") if p.is_file() and p.name != "verification.json"}
    write_json(output / "receipt.json", receipt)
    return {"id": task["id"], "arm": arm, "status": receipt["status"]}


def main():
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "run"))
    parser.add_argument("--directory", type=Path, default=root / "runs/natural-stage-v1")
    parser.add_argument("--arm", choices=("keyword", "model"), default="keyword")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--workers", type=int, choices=(1, 2), default=1)
    args = parser.parse_args()
    if args.action == "prepare": print(json.dumps(prepare(root, args.directory), ensure_ascii=False)); return
    _, tasks = load(root, args.directory)
    if args.arm == "model" and not args.live: raise ValueError("--live required")
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(slot, root, args.directory, t, args.arm, args.live) for t in tasks]
        for future in futures: print(json.dumps(future.result()), flush=True)


if __name__ == "__main__": main()
