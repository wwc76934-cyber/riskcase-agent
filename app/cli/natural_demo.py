"""Reproducible API demonstration; scope confirmation is explicitly simulated."""

import argparse
import json
from pathlib import Path
import secrets
from uuid import uuid4

from fastapi.testclient import TestClient

from app.risk.history import sha256_file
from app.risk.mixed_sql import SourceAudit
from app.service.api import create_app
from app.service.runtime import ResearchService, Settings, Worker, write_json
from scripts.verify_mixed import verify


def demonstrate(root, directory, *, live=False):
    directory.mkdir(parents=True, exist_ok=False)
    service = ResearchService(Settings(root, directory, {"example": root / "runs/mixed-stage-v1/cases/user-00/manifest.json"}, secrets.token_hex(32), allow_live=live))
    results = []
    with TestClient(create_app(service), headers={"Authorization": "Bearer " + service.settings.token}) as api:
        for index, text in enumerate(("查金额和新商户", "帮我看看异常", "冻结账户")):
            response = api.post("/jobs", json={"case_id": "example", "task_type": "natural", "mode": "live" if live else "offline", "question": {"text": text}}, headers={"Idempotency-Key": f"demo-submit-{index}"})
            response.raise_for_status(); identity = response.json()["id"]
            row = Worker(service).step()
            result = {"question": text, "job_id": identity, "proposal_state": row["state"], "scope_confirmation": "SIMULATED_CONFIRMATION_NOT_HUMAN_REVIEW", "states": ["queued", row["state"]]}
            if row["state"] == "awaiting_plan_confirmation":
                plan = api.get(f"/jobs/{identity}/artifacts/plan.json").json()
                result["checks"] = plan["route"]["checks"]
                confirmation = {"expected_version": row["version"], "result_hash": row["result_hash"], "plan_hash": row["result"]["plan_hash"], "decision": "confirm_plan"}
                reply = api.post(f"/jobs/{identity}/confirm-plan", json=confirmation, headers={"Idempotency-Key": f"demo-confirm-{index}"})
                reply.raise_for_status()
                row = Worker(service).step(); result["states"] += ["queued", row["state"]]
                with SourceAudit(root / "data/cohorts/cohort-3bfdbaab486f/build-report.json") as audit:
                    result["sql_verification"] = verify(service.artifact(identity, "summary.json"), audit)
                result["report"] = str(service.artifact(identity, "report.md"))
                # Report deliberately left awaiting review, not auto-accepted.
            result["events"] = service.store.events(identity)
            result.update(policy_approved=False, deployed=False)
            results.append(result)
    result = {"transport": "in_process_authenticated_HTTP_TestClient", "live": live, "data_kind": "IBM_TabFormer_synthetic",
              "scope_confirmation": "SIMULATED_CONFIRMATION_NOT_HUMAN_REVIEW", "scenarios": results,
              "jobs_sqlite_sha256": sha256_file(directory / "jobs.sqlite"), "policy_approved": False, "deployed": False}
    write_json(directory / "demo.json", result)
    return result


def main():
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    directory = args.output or root / "runs" / ("natural-demo-" + uuid4().hex)
    result = demonstrate(root, directory, live=args.live)
    print(json.dumps({"output_directory": str(directory), "live": args.live,
                      "scope_confirmation": result["scope_confirmation"],
                      "scenarios": [{k: r[k] for k in ("question", "states")} for r in result["scenarios"]]}, ensure_ascii=False, indent=2))


if __name__ == "__main__": main()
