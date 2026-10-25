"""Freeze a secondary sample-policy stress set before any paid mixed-task calls."""

from copy import deepcopy
import json
from pathlib import Path
import shutil

from app.risk.history import sha256_file
from app.risk.mixed_investigation import CHECKS, MixedQuestion
from app.risk.mixed_pack import load
from app.risk.mixed_sql import SourceAudit
from app.risk.source_merchant_pack import write_json


def main():
    root = Path(__file__).resolve().parents[1]
    primary = root / "runs/mixed-stage-v1"
    contract, original = load(root, primary)
    directory = root / "runs/mixed-sample-stress-v1"
    directory.mkdir(parents=True, exist_ok=False)
    selected, users = [], set()
    for task in original:
        if task["split"] == "development" and task["user_id"] not in users:
            selected.append(task); users.add(task["user_id"])
        if len(selected) == 2: break
    tasks = []
    with SourceAudit(Path(contract["cohort_report"])) as audit:
        for i, original_task in enumerate(selected):
            case = directory / "cases" / f"user-{i:02d}"
            case.mkdir(parents=True)
            for name in ("manifest.json", "facts.sqlite"): shutil.copyfile(Path(original_task["manifest"]).parent / name, case / name)
            question = MixedQuestion(CHECKS, 1000)
            manifest = json.loads((case / "manifest.json").read_text())
            gold = audit.gold(manifest, question)
            if gold["count_share"] != "insufficient_evidence" or gold["amount_share"] != "insufficient_evidence": raise ValueError("Expected sample-policy stress condition")
            tasks.append({**original_task, "id": f"u{i:02d}-sample_stress", "family": "sample_stress", "split": "evaluation",
                          "manifest": str((case / "manifest.json").resolve()), "manifest_sha256": sha256_file(case / "manifest.json"),
                          "question": {"checks": list(question.checks), "min_records": question.min_records}, "gold": gold})
    write_json(directory / "tasks-host-only.json", tasks)
    stress = deepcopy(contract)
    stress.update(users=2, tasks=2, tasks_sha256=sha256_file(directory / "tasks-host-only.json"),
                  primary_contract_sha256=sha256_file(primary / "contract.json"), selection="First two primary DEVELOPMENT users; same cases, explicit min_records=1000.",
                  limits=["Secondary predeclared sample-policy stress, not primary user-heldout evaluation or new users.",
                          "1000 is an intentionally strict test policy, not an industry threshold or new source-history gap.",
                          "Both share findings must be insufficient; other requested findings must still be answered.",
                          "Same frozen prompt/tools/budget as primary; no tuning/retry after paid execution.",
                          "Synthetic 2018 source; 2019 final test and fraud labels unopened."])
    write_json(directory / "contract.json", stress)
    print(json.dumps({"pack": str(directory), "tasks": len(tasks), "contract_sha256": sha256_file(directory / "contract.json")}, ensure_ascii=False))


if __name__ == "__main__": main()
