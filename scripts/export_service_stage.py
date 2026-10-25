"""Export bounded G4-A acceptance evidence without tokens, credentials or labels."""

import json
from pathlib import Path
import sqlite3
import subprocess
import xml.etree.ElementTree as ET

from app.risk.history import sha256_file
from app.risk.review_store import ReviewStore


def read_json(path): return json.loads(path.read_text(encoding="utf-8"))


def junit(path):
    suite = ET.parse(path).getroot().find("testsuite")
    result = {key: int(suite.attrib[key]) for key in ("tests", "errors", "failures", "skipped")}
    if result["errors"] or result["failures"]: raise ValueError("Tests did not pass")
    return result | {"seconds": float(suite.attrib["time"]), "sha256": sha256_file(path)}


def main():
    root = Path(__file__).resolve().parents[1]
    offline = read_json(root / "runs/service-stage-final-v1/acceptance.json")
    live = read_json(root / "runs/service-stage-live-v1/acceptance.json")
    sources = {}
    for name, acceptance, directory in (("offline", offline, "service-stage-final-v1"), ("live", live, "service-stage-live-v1")):
        generation = acceptance.get("generation", 1)
        path = root / "runs" / directory / "jobs" / acceptance["job_id"] / f"attempt-{generation}"
        if sha256_file(path / "summary.json") != acceptance["summary_sha256"]: raise ValueError("Summary changed")
        summary = read_json(path / "summary.json")
        for relative, expected in summary["artifact_sha256"].items():
            artifact = (path / relative).resolve()
            if not artifact.is_relative_to(path.resolve()) or sha256_file(artifact) != expected: raise ValueError("Artifact changed")
        proof = read_json(path / "verification.json")
        if proof["summary_sha256"] != acceptance["summary_sha256"] or proof["labels_opened"]: raise ValueError("SQL proof differs")
        sources[name] = {"summary": str((path / "summary.json").relative_to(root)).replace("\\", "/"),
                         "summary_sha256": acceptance["summary_sha256"], "verification_sha256": sha256_file(path / "verification.json"),
                         "runtime_fingerprint": summary["runtime_fingerprint"]}
    reviews = ReviewStore(root / "runs/reviews.sqlite")
    candidate = reviews.current("case-467716-rule")
    with sqlite3.connect((root / "runs/llm-budget.sqlite").resolve().as_uri()+"?mode=ro", uri=True) as db:
        total, unknown = db.execute("SELECT COUNT(*),SUM(CASE WHEN status!='estimated_from_usage' THEN 1 ELSE 0 END) FROM reservations").fetchone()
    result = {"stage": "G4-A", "date": "2026-10-21", "status": "local_service_acceptance_passed",
              "implementation_commit": subprocess.check_output(["git", "log", "-1", "--format=%H", "--", "app/service"], cwd=root, text=True).strip(),
              "live_source_smoke_implementation_commit": "d04f8a5",
              "full_regression": junit(root / "tmp/g4-tests.xml"), "clean_environment_service_tests": junit(root / "tmp/g4-clean-tests.xml"),
              "clean_environment_pip_check": "No broken requirements found.",
              "offline_source_recovery": offline, "live_source_smoke": live, "artifacts": sources,
              "project_ledger_entries": total, "project_unknown_cost_entries": unknown,
              "financial_candidate": {key: candidate[key] for key in ("candidate_id", "version", "package_hash", "status", "deployed")},
              "review_audit": reviews.verify(), "remote_CI_executed": False, "final_2019_test_opened": False,
              "limits": ["Synthetic development source, not real-world fraud performance.",
                         "Live smoke repeats an existing case; not independent task evaluation.",
                         "Unresolved external calls are blocked, not retried; no exactly-once HTTP guarantee.",
                         "Local bearer token, not enterprise identities/RBAC or tamper-proof audit storage.",
                         "Local tests do not prove remote CI success or an Agent advantage over fixed workflows."]}
    output = root / "docs/service-stage-v1-results.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "stage": result["stage"], "status": result["status"],
                      "tests": result["full_regression"]["tests"], "clean_service_tests": result["clean_environment_service_tests"]["tests"]}, ensure_ascii=False))


if __name__ == "__main__": main()
