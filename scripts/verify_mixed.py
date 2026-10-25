"""Check complete source projection, every tool observation and failure-inclusive results."""

import argparse
import json
from pathlib import Path

from app.risk.history import sha256_file
from app.risk.mixed_investigation import MixedQuestion, check_answer, query_plan, render_report
from app.risk.mixed_sql import SourceAudit
from app.risk.source_merchant_pack import write_json


def verify(summary_path, audit):
    directory = Path(summary_path).parent
    summary = json.loads(Path(summary_path).read_text(encoding="utf-8"))
    required = {"case/manifest.json", "case/facts.sqlite", "trace.json", "question.json", "policy.json"}
    if not required <= set(summary["artifact_sha256"]): raise ValueError("Missing artifacts")
    for name, expected in summary["artifact_sha256"].items():
        path = (directory / name).resolve()
        if not path.is_relative_to(directory.resolve()) or sha256_file(path) != expected: raise ValueError("Artifact changed")
    manifest = json.loads((directory / "case/manifest.json").read_text(encoding="utf-8"))
    rows = audit.projection(manifest, directory / "case/facts.sqlite")
    question = MixedQuestion(**json.loads((directory / "question.json").read_text(encoding="utf-8")))
    if json.loads(json.dumps(question.__dict__)) != summary["question"]: raise ValueError("Question differs")
    trace = json.loads((directory / "trace.json").read_text(encoding="utf-8"))
    for key in ("status", "findings", "first_attempt", "format_repair", "failure_stage", "client_requests", "tool_attempts", "maximum_client_requests"):
        if trace[key] != summary[key]: raise ValueError("Trace summary mismatch")
    if trace["tool_attempts"] != len(trace["tool_trace"]) or trace["tool_attempts"] > 6 or trace["client_requests"] > 6: raise ValueError("Budget mismatch")
    observed, queries, seen = {}, set(), {}
    for entry in trace["tool_trace"]:
        key = (entry["tool"], json.dumps(entry["arguments"], sort_keys=True))
        if entry.get("cache_hit") and seen.get(key) != entry["result"]: raise ValueError("Cache lacks matching observation")
        if not entry["result"]["ok"]: continue
        expected = audit.observation(manifest, entry["tool"], entry["arguments"])
        if entry["result"]["data"] != expected: raise ValueError("SQL observation mismatch")
        observed[expected["evidence_id"]] = expected
        seen[key] = entry["result"]; queries.add(key)
    if observed != trace["evidence"]: raise ValueError("Unobserved evidence")
    if summary["cache_hits"] != sum(bool(e.get("cache_hit")) for e in trace["tool_trace"]): raise ValueError("Cache count differs")
    gold = audit.gold(manifest, question)
    passed = trace["status"] == "completed_validated"
    if passed:
        body, stage = check_answer(json.dumps(trace["findings"]), observed, question)
        if stage is not None or {r["check"]: r["assessment"] for r in body["findings"]} != gold: raise ValueError("Final answer differs from SQL")
        if "report.md" not in summary["artifact_sha256"] or render_report(body, observed, question) != (directory / "report.md").read_text(encoding="utf-8"): raise ValueError("Report differs")
    elif "report.md" in summary["artifact_sha256"] or trace["findings"] is not None: raise ValueError("Failed task published findings")
    first_passed = summary["arm"] == "fixed"
    if summary["arm"] != "fixed":
        answers = [m for m in trace["messages"] if m["role"] == "assistant" and not m.get("tool_calls")]
        if trace["first_attempt"] is not None:
            if not answers: raise ValueError("Missing first answer")
            first, stage = check_answer(answers[0].get("content"), observed, question)
            expected_first = {"status": "completed_validated" if stage is None else "answer_rejected", "failure_stage": stage, "findings": first}
            if trace["first_attempt"] != expected_first: raise ValueError("First answer record differs")
            first_passed = stage is None
        if trace["format_repair"]["attempted"]:
            if trace["first_attempt"]["failure_stage"] != "answer_format" or len(answers) != 2 or trace["messages"][-1] != answers[-1] or trace["messages"][-2]["role"] != "user": raise ValueError("Invalid repair transition")
        if passed:
            final, stage = check_answer(answers[-1].get("content"), observed, question)
            if stage is not None or final != trace["findings"]: raise ValueError("Published answer differs from transcript")
    needed = {(name, json.dumps(args, sort_keys=True)) for name, args in query_plan(question)}
    proof = {"status": "source_sql_verified", "summary_sha256": sha256_file(summary_path), "projected_rows": rows,
             "observations_checked": len(trace["tool_trace"]), "successful_distinct_queries": len(queries), "required_queries": len(needed),
             "extra_distinct_queries": len(queries-needed), "missing_required_queries": len(needed-queries),
             "answer_published": passed, "task_passed": passed, "first_attempt_passed": first_passed,
             "gold": gold, "labels_opened": False}
    write_json(directory / "verification.json", proof)
    return proof


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", required=True, type=Path)
    parser.add_argument("--cohort-report", type=Path, default=root / "data/cohorts/cohort-3bfdbaab486f/build-report.json")
    args = parser.parse_args()
    with SourceAudit(args.cohort_report) as audit: print(json.dumps(verify(args.summary, audit), indent=2))


if __name__ == "__main__": main()
