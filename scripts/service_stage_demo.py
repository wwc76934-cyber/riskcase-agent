"""Source-case offline acceptance: persist, hard exit, recover and independently audit."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from app.risk.history import sha256_file
from app.service.runtime import ResearchService, Settings, write_json
from scripts.verify_merchant_research import verify


def main(argv=None):
    repo = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--case-manifest", type=Path, default=repo / "runs/case-v1-f4a58a923f32/case/manifest.json")
    parser.add_argument("--cohort-report", type=Path, default=repo / "data/cohorts/cohort-3bfdbaab486f/build-report.json")
    args = parser.parse_args(argv)
    directory = args.directory.resolve()
    directory.mkdir(parents=True, exist_ok=False)
    import secrets
    token = secrets.token_urlsafe(32)
    (directory / "auth.token").write_text(token, encoding="ascii")
    catalog = directory / "catalog.json"
    write_json(catalog, {"development": str(args.case_manifest.resolve())})
    service = ResearchService(Settings(repo, directory, {"development": args.case_manifest}, token))
    identity, _ = service.submit("source-recovery-0001", case_id="development", question={"min_records": 10})
    base = [sys.executable, "-X", "utf8", "-B", "-m", "app.cli.service", "worker", "--directory", str(directory), "--catalog", str(catalog), "--once"]
    creation = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    stopped = subprocess.run(base + ["--offline-stop-after-call", "2", "--offline-lease-seconds", "2"], cwd=repo, capture_output=True, timeout=30, creationflags=creation)
    if stopped.returncode != 75: raise ValueError("Fault injection did not reach durable boundary")
    time.sleep(2.1)
    recovered = subprocess.run(base, cwd=repo, capture_output=True, timeout=30, creationflags=creation)
    if recovered.returncode: raise ValueError("Recovery worker failed")
    row = service.store.get(identity)
    if row["state"] != "awaiting_review" or row["generation"] != 2 or row["result"]["replayed_client_responses"] != 2:
        raise ValueError("Recovery acceptance failed")
    summary = service.artifact(identity, "summary.json")
    proof = verify(summary, args.cohort_report)
    cancelled, _ = service.submit("source-cancel-0001", case_id="development", question={"min_records": 1})
    service.store.cancel(cancelled)
    write_json(directory / "acceptance.json", {"status": "offline_source_recovery_verified", "job_id": identity,
        "state": row["state"], "generation": row["generation"], "replayed_client_responses": 2,
        "new_client_requests_second_attempt": row["result"]["new_client_requests"], "real_LLM_requests": 0,
        "hard_exit_code": stopped.returncode, "cancelled_job_state": service.store.get(cancelled)["state"],
        "source_sql_proof": proof, "summary_sha256": sha256_file(summary),
        "policy_approved": False, "deployed": False, "final_test_opened": False})
    print((directory / "acceptance.json").read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__": raise SystemExit(main())
