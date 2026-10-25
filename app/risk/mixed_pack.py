"""Label-blind new source users and frozen mixed-task contract."""

from dataclasses import asdict
import json
from pathlib import Path

from app.risk.case_snapshot import export_case
from app.risk.history import sha256_file
from app.risk.mixed_investigation import CHECKS, MixedQuestion
from app.risk.mixed_sql import SourceAudit
from app.risk.source_merchant_pack import select_users, write_json


FAMILIES = {"frequency": ("frequency",), "amount_merchant": ("amount", "merchant"),
            "concentration": ("count_share", "amount_share"), "full_counterevidence": CHECKS}
CODE = ["app/risk/mixed_investigation.py", "app/risk/mixed_sql.py", "app/risk/mixed_pack.py",
        "app/cli/mixed_investigation.py", "app/cli/mixed_benchmark.py", "scripts/verify_mixed.py",
        "app/risk/investigation.py", "app/risk/merchant_research.py", "app/risk/merchant_stats.py", "app/risk/case_snapshot.py",
        "app/tools/case_tools.py", "app/agent/react_agent.py", "app/llm/glm_client.py", "app/llm/siliconflow_client.py", "app/llm/cost_ledger.py"]


def build(root, directory, *, count=8):
    if type(count) is not int or count < 4 or count % 2: raise ValueError("Even user count >=4 required")
    old_tasks = root / "runs/source-merchant-stage-v1/tasks-host-only.json"
    excluded = {"38"} | {t["user_id"] for t in json.loads(old_tasks.read_text(encoding="utf-8"))}
    detector_path = root / "runs/ml-v1-90f95014b365/report.json"
    detector = json.loads(detector_path.read_text(encoding="utf-8")); queue = detector_path.parent / "daily-review.jsonl"
    if detector["final_test_read"] or sha256_file(queue) != detector["artifact_sha256"][queue.name]: raise ValueError("Frozen development detector required")
    cohort_path = root / "data/cohorts/cohort-3bfdbaab486f/build-report.json"
    with SourceAudit(cohort_path) as audit:
        candidates = []
        for line in queue.read_text(encoding="utf-8").splitlines():
            item = json.loads(line)
            if item["strategy"] != "hist_gradient_boosting": continue
            row = audit.db.execute("SELECT user_id,event_time FROM transactions WHERE transaction_id=? AND split='validation' AND event_time>='2018-01-01' AND event_time<'2019-01-01'", (item["transaction_id"],)).fetchone()
            if row is None or row["event_time"] != item["event_time"]: raise ValueError("Queue/source mismatch")
            candidates.append({key: item[key] for key in ("transaction_id", "event_time", "score", "day_rank")} | {"user_id": row["user_id"]})
        selected = select_users(candidates, count=count, seed="riskcase-mixed-v1", excluded=excluded)
        directory.mkdir(parents=True, exist_ok=False)
        tasks = []
        for index, item in enumerate(selected):
            manifest = export_case(directory / "cases" / f"user-{index:02d}", source_database=audit.source, expected_source_sha256=audit.source_hash,
                transaction_id=item["transaction_id"], alert={"transaction_id": item["transaction_id"], "score": item["score"], "day_rank": item["day_rank"],
                    "selection": "Earliest HGB queued alert per new task user; label-blind seeded user order."},
                coverage_start=audit.cohort["coverage"]["warmup_start"], coverage_end=audit.cohort["coverage"]["end"],
                full_source_scanned=audit.cohort["source_csv_fully_scanned"], source_report_sha256=audit.report_hash)
            case = json.loads(manifest.read_text(encoding="utf-8"))
            audit.projection(case, manifest.parent / "facts.sqlite")
            for family, checks in FAMILIES.items():
                question = MixedQuestion(checks, 10)
                tasks.append({"id": f"u{index:02d}-{family}", "family": family, "split": "development" if index < count//2 else "evaluation",
                              "user_id": item["user_id"], "transaction_id": item["transaction_id"],
                              "manifest": str(manifest.resolve()), "manifest_sha256": sha256_file(manifest), "projection_sha256": case["projection_sha256"],
                              "question": asdict(question), "gold": audit.gold(case, question)})
    write_json(directory / "tasks-host-only.json", tasks)
    contract = {"version": 1, "users": count, "tasks": len(tasks), "arms": ["fixed", "evidence_only", "agent"],
                "data_kind": "IBM_TabFormer_synthetic", "seed": "riskcase-mixed-v1", "excluded_old_task_users": sorted(excluded),
                "old_tasks_sha256": sha256_file(old_tasks), "tasks_sha256": sha256_file(directory / "tasks-host-only.json"),
                "cohort_report": str(cohort_path.resolve()), "cohort_report_sha256": sha256_file(cohort_path), "queue_sha256": sha256_file(queue),
                "policy_sha256": sha256_file(root / "config/siliconflow-live-v1.json"),
                "code_file_sha256": {name: sha256_file(root / name) for name in CODE},
                "max_requests": 6, "max_tool_attempts": 6, "format_repairs": 1,
                "baseline": "Question-aware fixed query plan, no gold or labels; same tools/validators as Agent.",
                "limits": [f"{len(tasks)} correlated tasks from {count} users, not {len(tasks)} independent users.",
                           "New task users, not new detector-training users; source period is still 2018 development.",
                           "2019 final detector test sealed; no fraud labels used for case selection or task gold.",
                           "All source snapshots retain full available history; incomplete-history behavior also tested in explicit fixtures.",
                           "No tuning after freeze or rerunning failed/uncertain paid slots. Model scores are not fraud probabilities."]}
    write_json(directory / "contract.json", contract)
    return contract


def load(root, directory):
    contract = json.loads((directory / "contract.json").read_text(encoding="utf-8"))
    if sha256_file(directory / "tasks-host-only.json") != contract["tasks_sha256"]: raise ValueError("Tasks changed")
    for name, expected in contract["code_file_sha256"].items():
        if sha256_file(root / name) != expected: raise ValueError("Code changed after freeze: " + name)
    if sha256_file(root / "config/siliconflow-live-v1.json") != contract["policy_sha256"] or sha256_file(Path(contract["cohort_report"])) != contract["cohort_report_sha256"]: raise ValueError("Source/policy changed")
    tasks = json.loads((directory / "tasks-host-only.json").read_text(encoding="utf-8"))
    dev, evaluation = ({t["user_id"] for t in tasks if t["split"] == split} for split in ("development", "evaluation"))
    if dev & evaluation or (dev | evaluation) & set(contract["excluded_old_task_users"]): raise ValueError("Task-user leakage")
    for task in tasks:
        path = Path(task["manifest"])
        if not path.resolve().is_relative_to(directory.resolve()) or sha256_file(path) != task["manifest_sha256"] or sha256_file(path.parent / "facts.sqlite") != task["projection_sha256"]: raise ValueError("Case changed")
    return contract, tasks
