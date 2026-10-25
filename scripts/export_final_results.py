"""Assemble a compact local research delivery receipt from already-verified artifacts.

No inference, outcome evaluation, model selection or network requests occur here.
This checks receipt bindings; it does not replace independent numerical audits.
"""

import argparse
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import platform
import sqlite3
import subprocess
import xml.etree.ElementTree as ET


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""): h.update(block)
    return h.hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def bound(base, name, expected):
    base = Path(base).resolve()
    path = (base / name).resolve()
    if not path.is_relative_to(base) or not path.is_file() or sha(path) != expected:
        raise ValueError("Artifact binding differs: " + name)


def check_nested_artifacts(directory, receipt):
    artifacts = receipt.get("artifact_sha256")
    if not isinstance(artifacts, dict) or not artifacts:
        raise ValueError("Recorded child artifacts required")
    for name, expected in artifacts.items(): bound(directory, name, expected)


def check_predictions(directory, manifest):
    bound(directory, "predictions.sqlite", manifest["predictions_sha256"])


def checked_tests(path):
    tree = ET.parse(path)
    cases = list(tree.iter("testcase"))
    counts = {name: sum(case.find(name) is not None for case in cases) for name in ("failure", "error", "skipped")}
    if not cases or any(counts.values()): raise ValueError("Nonempty passing test receipt required")
    suites = list(tree.iter("testsuite"))
    return {"tests": len(cases), "passed": len(cases), "failures": counts["failure"], "errors": counts["error"],
            "skipped": counts["skipped"], "suite_seconds": sum(float(s.attrib.get("time", 0)) for s in suites), "sha256": sha(path)}


def assemble(root):
    root = Path(root).resolve()
    final = root / "runs/final-detector-v1"
    contract, detector, independent = (read(final / name) for name in ("contract.json", "report.json", "independent-audit.json"))
    if (detector["status"] != "final_time_holdout_evaluated" or detector["selection_after_final_test"] or detector["deployed"]
            or independent["status"] != "independent_metrics_and_review_membership_verified"):
        raise ValueError("Final detector proof required")
    if sha(final / "report.json") != independent["report_sha256"]: raise ValueError("Detector report changed after audit")
    for file, field in (("contract.json", "contract_sha256"), ("predictions.json", "predictions_manifest_sha256"),
                        ("outcomes-opened.json", "outcomes_opened_sha256")):
        if sha(final / file) != detector[field]: raise ValueError("Final detector protocol binding differs")
    for name, expected in contract["code_file_sha256"].items(): bound(root, name, expected)
    for key in ("features_manifest", "features", "cohort_report", "labels"):
        source = Path(contract[key]).resolve()
        bound(root, str(source.relative_to(root)), contract[key + "_sha256"])
    cohort = read(contract["cohort_report"])
    for name, expected in cohort["artifact_sha256"].items():
        bound(Path(contract["cohort_report"]).parent, name, expected)
    check_predictions(final, read(final / "predictions.json"))
    for model in contract["models"]:
        for artifact in model["artifacts"].values(): bound(final, artifact["path"], artifact["sha256"])

    dev = root / "runs/neural-v2-development-01"
    devproof = read(dev / "verification.json")
    if devproof["status"] != "verified" or sha(dev / "report.json") != devproof["report_sha256"]:
        raise ValueError("Development proof differs")

    agent_dir = root / "runs/planner-v2-independent-scoring"
    agent = read(agent_dir / "summary.json")
    if agent["status"] != "independent_receipt_scores_and_counts_verified" or not agent["all_prior_counts_match"]:
        raise ValueError("Independent planner proof required")
    originals = root / "runs/planner-v2-acceptance"
    for name, expected in agent["receipt_and_proof_sha256"].items():
        bound(originals, name, expected)
        if name.endswith("/receipt.json"):
            receipt_path = originals / name
            receipt = read(receipt_path)
            # Failed slots can legitimately have no published evidence. Their
            # saved route/request artifacts, if present, must still be bound.
            if receipt.get("artifact_sha256"): check_nested_artifacts(receipt_path.parent, receipt)
    for name, key in (("contract.json", "run_contract_sha256"), ("audit.json", "prior_audit_sha256"), ("audit-summary.json", "prior_summary_sha256")):
        bound(originals, name, agent[key])
    bound(root, "config/acceptance-intent-v2-host-only.json", agent["dataset_sha256"])
    bound(root, "scripts/audit_acceptance_receipts.py", agent["script_sha256"])
    bound(root, "scripts/score_acceptance_intent.py", agent["scorer_sha256"])
    for method, group in agent["groups"].items():
        bound(agent_dir, method + "-records.json", group["records_sha256"])
        bound(agent_dir, method + "-score.json", group["score_sha256"])
    if agent["slots"] != 400 or agent["receipts"] != 400 or agent["new_model_requests"] or agent["paid_slots_retried"]:
        raise ValueError("Complete non-retried planner slots required")

    from scripts.export_capacity_v2 import verified_evidence
    _, _, capacity, capproof = verified_evidence(root, root / "runs/capacity-neural-v2")
    from app.cli.release_demo import verify_package
    release_dir = root / "runs/release-demo-v1"
    release_proof = verify_package(release_dir)
    release = read(release_dir / "manifest.json")
    if (release["status"] != "neural_investigation_release_prepared" or release["labels_read"]
            or release["final_2019_records_selected"] or release["real_LLM_requests"]
            or release["policy_approved"] or release["deployed"]):
        raise ValueError("Offline no-outcome release integration required")
    if any(c["state"] != "awaiting_review" or c["sql_verification"]["status"] != "source_sql_verified" for c in release["cases"]):
        raise ValueError("Source checked reports must await review")

    ui = read(root / "docs/workbench-browser-qa.json")
    if ui["status"] != "passed" or any(ui[k] for k in ("console_errors", "page_errors", "request_failures")):
        raise ValueError("Passing browser QA required")
    bound(root, "scripts/verify_workbench_browser.cjs", ui["script_sha256"])
    for name, expected in ui["screenshot_sha256"].items(): bound(root / "runs/workbench-ui-final-qa", name, expected)
    release_ui = read(root / "docs/assets/neural-workbench.qa.json")
    if (release_ui["status"] != "passed" or release_ui["registered_cases"] != 4
            or release_ui["final_state_counts"] != {"awaiting_review": 4}
            or any(release_ui[k] for k in ("console_errors", "page_errors", "request_failures", "mutating_browser_requests",
                                          "external_browser_requests", "real_LLM_requests", "live_enabled"))
            or release_ui["service_database_sha256_before"] != release_ui["service_database_sha256_after"]):
        raise ValueError("Read-only neural integration UI proof required")
    bound(root, "scripts/verify_release_browser.cjs", release_ui["script_sha256"])
    bound(root, release_ui["screenshot"], release_ui["screenshot_sha256"])
    bound(release_dir, "manifest.json", release_ui["release_manifest_sha256"])

    # Read-only database access: never initialize/migrate/decide a real candidate.
    from app.risk.review_store import ReviewStore
    review_path = root / "runs/reviews.sqlite"
    before = sha(review_path)
    with closing(sqlite3.connect(review_path.as_uri() + "?mode=ro", uri=True)) as db:
        states, head, events = ReviewStore._verify(db)
    state = states["case-467716-rule"]
    if state["version"] != 2 or state["status"] != "pending_review" or state["deployed"] or sha(review_path) != before:
        raise ValueError("Real financial candidate must remain unchanged and pending")
    with closing(sqlite3.connect((root / "runs/llm-budget.sqlite").as_uri() + "?mode=ro", uri=True)) as db:
        entries = db.execute("SELECT reserved_cny,estimated_cny,status FROM reservations").fetchall()
    known = sum((Decimal(row[1]) for row in entries if row[1] is not None), Decimal(0))
    uncertain = sum((Decimal(row[0]) for row in entries if row[1] is None), Decimal(0))
    budget = {"currency": "CNY", "reservations": len(entries), "known_usage_estimates_cny": str(known),
              "uncertain_reservations_cny": str(uncertain), "committed_estimate_cny": str(known + uncertain),
              "unknown_cost_requests": sum(row[2] != "estimated_from_usage" for row in entries),
              "authorization_cap_cny": "20", "provider_billing_verified": False}
    if known + uncertain > Decimal(20): raise ValueError("Authorized estimated budget exceeded")
    tests = {"full": checked_tests(root / "runs/project-final-v1/full-tests.xml"),
             "clean_service": checked_tests(root / "runs/project-final-v1/clean-service-tests.xml")}
    tests["clean_service"]["scope"] = "Selected service/intent/protocol fixtures; no NumPy/PyTorch installed. Not full clean ML training."

    compact_models = {name: {"average_precision": values["average_precision"],
                             "daily_review": {k: {field: v for field, v in values["daily_review"][k].items() if field != "groups"}
                                              for k in ("5", "10", "20")}}
                      for name, values in detector["strategies"].items()}
    metric_keys = ("tasks", "exact_scope_matches", "first_scope_matches", "false_execute_on_nonexecute", "routing_failures",
                   "extra_checks", "missing_checks", "overabstention_on_execute", "real_requests", "estimated_microyuan",
                   "contract_corrections", "conditional_sql_verified_reports")
    sources = ["runs/final-detector-v1/contract.json", "runs/final-detector-v1/report.json", "runs/final-detector-v1/independent-audit.json",
               "runs/neural-v2-development-01/report.json", "runs/neural-v2-development-01/verification.json",
               "runs/planner-v2-independent-scoring/summary.json", "runs/capacity-neural-v2/report.json", "runs/capacity-neural-v2/verification.json",
               "runs/release-demo-v1/manifest.json", "docs/workbench-browser-qa.json", "docs/assets/neural-workbench.qa.json",
               "runs/project-final-v1/full-tests.xml", "runs/project-final-v1/clean-service-tests.xml"]
    source_files = {name: sha(root / name) for name in sources}
    code_files = {str(p.relative_to(root)).replace("\\", "/"): sha(p)
                  for folder in ("app", "scripts", "tests") for p in sorted((root / folder).rglob("*"))
                  if p.is_file() and p.suffix in (".py", ".ps1", ".cjs")}
    snapshot = root / "runs/project-final-v1/code-snapshot.json"
    snapshot.write_text(json.dumps(code_files, indent=2) + "\n", encoding="utf-8")
    return {"release": "RiskCase Agent V1 local research delivery", "generated_at": datetime.now(timezone.utc).isoformat(),
            "delivery_scope": "local_synthetic_research_workbench_not_enterprise_production", "remote_ci_executed": False,
            "git_head_at_export": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
            "code_snapshot_sha256": sha(snapshot), "code_snapshot_files": len(code_files),
            "environment": {"python": platform.python_version(), "platform": platform.platform(),
                            "packages": {p: version(p) for p in ("torch", "numpy", "pandas", "scikit-learn", "fastapi", "uvicorn", "pytest")}},
            "tests": tests, "browser": {"status": ui["status"], "assertions": len(ui["assertions"]), "browser_version": ui["browser_version"], "live_enabled": False},
            "neural_browser": {key: release_ui[key] for key in ("status", "registered_cases", "final_state_counts", "mutating_browser_requests",
                                                                 "external_browser_requests", "real_LLM_requests", "live_enabled", "browser_version")},
            "detector": {"period": detector["period"], "rows": detector["rows"], "positives": detector["positives"],
                         "primary_model": detector["primary_model_frozen_before_test"], "primary_budget": detector["primary_budget"],
                         "strategies": compact_models, "primary_paired_day_sensitivity": detector["paired_comparison"][detector["primary_model_frozen_before_test"]],
                         "selection_after_final_test": False, "limits": detector["limits"]},
            "agent": {"questions": 100, "slots": agent["slots"], "receipts": agent["receipts"],
                      "groups": {name: {key: g.get(key, 0) for key in metric_keys} for name, g in agent["groups"].items()},
                      "conditional_sql_reports": agent["conditional_sql_reports"], "normalization": agent["input_text_normalization"],
                      "limits": agent["limits"]},
            "capacity_development": {"period": "2018", "baseline_kind": capacity["baseline_kind"], "metrics": capacity["metrics"],
                                     "verification": capproof, "research_review_status": capacity["research_review_status"]},
            "integration": {"period": "2018", "alignment": release["alignment"], "queue_rows": release["queue_rows"],
                            "representative_users": release["representative_users"], "proof": release_proof,
                            "cases": [{key: c[key] for key in ("case_id", "state", "client_mode", "scope_confirmation")} for c in release["cases"]],
                            "real_LLM_requests": 0, "labels_read": False},
            "real_candidate": {"version": state["version"], "status": state["status"], "package_hash": state["package_hash"],
                               "deployed": False, "database_sha256": before, "events": events, "event_head": head},
            "estimated_model_budget": budget, "source_sha256": source_files,
            "export_limits": ["Binding checks on prior proofs, not another independent numerical or model-inference implementation.",
                              "Byte hashes describe this local checkout; newline transformations may differ across checkouts.",
                              "No external data rights, real-user efficiency, enterprise identity or production validation is inferred."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = assemble(args.root)
    output = args.output or args.root / "docs/project-final-v1-results.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "tests": result["tests"], "budget": result["estimated_model_budget"],
                      "scope": result["delivery_scope"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__": main()
