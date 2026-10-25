"""Frozen mixed tasks; serial paid slots with receipts and independent source audit."""

import argparse
from collections import Counter
from decimal import Decimal
import json
from pathlib import Path
import subprocess
import sys
import time

from app.risk.history import sha256_file
from app.risk.mixed_investigation import MixedQuestion
from app.risk.mixed_pack import build, load
from app.risk.mixed_sql import SourceAudit
from app.risk.source_merchant_pack import write_json
from scripts.verify_mixed import verify


def aggregate(records):
    result = {}
    for arm in ("fixed", "evidence_only", "agent"):
        rows = [r for r in records if r["arm"] == arm]
        result[arm] = {"tasks": len(rows), "passed": sum(r.get("proof", {}).get("task_passed", False) for r in rows),
            "first_attempt_passed": sum(r.get("proof", {}).get("first_attempt_passed", False) for r in rows),
            "real_requests": sum(r.get("real_requests") or 0 for r in rows), "unknown_request_slots": sum(r.get("real_requests") is None for r in rows),
            "tool_attempts": sum(r.get("tool_attempts", 0) for r in rows),
            "extra_distinct_queries": sum(r.get("proof", {}).get("extra_distinct_queries", 0) for r in rows),
            "estimated_cny": str(sum((Decimal(r.get("estimated_cny", "0")) for r in rows), Decimal(0))),
            "statuses": dict(Counter(r["status"] for r in rows)),
            "by_family": {family: {"tasks": sum(r["family"] == family for r in rows), "passed": sum(r["family"] == family and r.get("proof", {}).get("task_passed", False) for r in rows)} for family in sorted({r["family"] for r in rows})}}
    return result


def run(root, pack, output, *, split, live=False):
    contract, tasks = load(root, pack)
    if split not in ("development", "evaluation"): raise ValueError("Explicit split required")
    binding = {"contract_sha256": sha256_file(pack / "contract.json"), "split": split, "live": live}
    if output.exists():
        if json.loads((output / "binding.json").read_text()) != binding: raise ValueError("Different batch binding")
    else:
        output.mkdir(parents=True, exist_ok=False); write_json(output / "binding.json", binding)
    with SourceAudit(Path(contract["cohort_report"])) as audit:
        for task in (t for t in tasks if t["split"] == split):
            for arm in contract["arms"]:
                receipt = output / f"{task['id']}-{arm}.json"
                if receipt.exists(): continue  # Failure and uncertain receipts are also final attempts.
                load(root, pack)
                directory = output / "runs" / f"{task['id']}-{arm}"
                record = {"task_id": task["id"], "family": task["family"], "arm": arm, "status": "running",
                          "real_requests": None if live and arm != "fixed" else 0, "source_sql_verified": False}
                write_json(receipt, record)
                start = time.perf_counter()
                args = [sys.executable, "-X", "utf8", "-B", "-m", "app.cli.mixed_investigation", "--case-manifest", task["manifest"],
                        "--checks", *task["question"]["checks"], "--min-records", str(task["question"]["min_records"]), "--arm", arm, "--output", str(directory)]
                if live and arm != "fixed": args.append("--live")
                try:
                    process = subprocess.run(args, cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=210)
                    summary_path = directory / "summary.json"
                    if not summary_path.exists(): raise ValueError("No complete task summary")
                    summary = json.loads(summary_path.read_text(encoding="utf-8"))
                    record.update(status=summary["status"], summary_path=str(summary_path), summary_sha256=sha256_file(summary_path),
                                  real_requests=summary["real_LLM_requests"], tool_attempts=summary["tool_attempts"],
                                  client_requests=summary["client_requests"], format_repair=summary["format_repair"])
                    if (directory / "usage.json").exists():
                        record["estimated_cny"] = json.loads((directory / "usage.json").read_text())["ledger"]["run_committed_estimate_cny"]
                    proof = verify(summary_path, audit)
                    if proof["gold"] != task["gold"]: raise ValueError("Frozen gold changed")
                    record.update(status=summary["status"], summary_path=str(summary_path), summary_sha256=sha256_file(summary_path),
                                  proof=proof, source_sql_verified=True, real_requests=summary["real_LLM_requests"], tool_attempts=summary["tool_attempts"],
                                  client_requests=summary["client_requests"], format_repair=summary["format_repair"])
                except KeyboardInterrupt:
                    record["status"] = "interrupted_charge_unknown"; write_json(receipt, record); raise
                except Exception as exc:
                    record.update(status="failed_or_interrupted", error_type=type(exc).__name__)
                record["seconds"] = round(time.perf_counter()-start, 3)
                write_json(receipt, record)
                print(json.dumps({"task": task["id"], "arm": arm, "status": record["status"], "passed": record.get("proof", {}).get("task_passed", False)}), flush=True)
    records = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(output.glob("u*.json"))]
    report = {**binding, "recorded_slots": len(records), "expected_slots": sum(t["split"] == split for t in tasks)*3,
              "aggregate": aggregate(records), "records": records, "limits": contract["limits"]}
    write_json(output / "report.json", report)
    return report


def main():
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("build", "run")); parser.add_argument("--pack", type=Path, required=True)
    parser.add_argument("--output", type=Path); parser.add_argument("--split", choices=("development", "evaluation"), default="development")
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    if args.action == "build": print(json.dumps(build(root, args.pack), indent=2))
    else:
        if args.output is None: parser.error("--output required")
        print(json.dumps(run(root, args.pack, args.output, split=args.split, live=args.live)["aggregate"], indent=2))


if __name__ == "__main__": main()
