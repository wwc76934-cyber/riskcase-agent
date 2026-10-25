"""Bind a review to the proposal, replay, provenance and both verification records."""

import json
from pathlib import Path

from app.risk.candidate_rules import compile_candidate
from app.risk.history import sha256_file


def review_package(summary_path: Path):
    source = summary_path.parent
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if summary["status"]!="candidate_validated": raise ValueError("Validated candidate required")
    for name,digest in summary["artifact_sha256"].items():
        if Path(name).name!=name or sha256_file(source / name)!=digest: raise ValueError("Changed proposal material")
    proposal_path = source / "proposal.json"
    proposal = json.loads(proposal_path.read_text(encoding="utf-8"))
    compile_candidate(proposal["rule"])
    if proposal["status"]!="candidate_validated" or proposal["human_approved"] or proposal["deployed"]:
        raise ValueError("Unapproved draft source required; decisions belong in review records")
    proof_path = source / "proposal-verification.json"
    proof = json.loads(proof_path.read_text(encoding="utf-8"))
    if proof["status"]!="passed" or proof["summary_sha256"]!=sha256_file(summary_path) or not proof["replay_link_checked"]:
        raise ValueError("Current proposal verification required")
    replay_dir = Path(summary["replay"]["output_directory"])
    report_path,frozen_path,sql_path = replay_dir / "report.json",replay_dir / "frozen-candidate.json",replay_dir / "sql-verification.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    frozen = json.loads(frozen_path.read_text(encoding="utf-8"))
    sql = json.loads(sql_path.read_text(encoding="utf-8"))
    if report["status"]!="development_replay_completed" or report["final_test_evaluated"] is not False:
        raise ValueError("Development replay required")
    if report["metrics"]!=summary["replay"]["metrics"] or frozen["rule"]!=proposal["rule"]:
        raise ValueError("Rule/report linkage mismatch")
    if sha256_file(frozen_path)!=report["frozen_candidate_sha256"] or frozen["proposal_sha256"]!=sha256_file(proposal_path):
        raise ValueError("Changed frozen candidate")
    if sql["status"]!="passed" or sql["report_sha256"]!=sha256_file(report_path): raise ValueError("Current SQL verification required")
    return {"proposal_summary_sha256":sha256_file(summary_path),"proposal_sha256":sha256_file(proposal_path),
        "replay_report_sha256":sha256_file(report_path),"frozen_candidate_sha256":sha256_file(frozen_path),
        "proposal_verification_sha256":sha256_file(proof_path),"sql_verification_sha256":sha256_file(sql_path),
        "rule":proposal["rule"],"metrics":report["metrics"],"data_kind":report["data_kind"],
        "scope":"Unapproved local research package; not a production policy or business benefit endorsement"}
