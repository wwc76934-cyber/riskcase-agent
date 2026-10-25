"""Independently recompute saved HTTP usage estimates; not a billing reconciliation."""

import argparse
from decimal import Decimal
import json
from pathlib import Path
import sqlite3

from app.risk.history import sha256_file


def verify(summary_path):
    run = summary_path.parent
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    for name,digest in summary["artifact_sha256"].items():
        if sha256_file(run / name) != digest:
            raise ValueError(f"Changed artifact: {name}")
    policy = json.loads((run / "policy.json").read_text(encoding="utf-8"))
    audit = json.loads((run / "usage.json").read_text(encoding="utf-8"))
    entries, requests = audit["ledger"]["entries"], audit["request_audit"]
    if not len(entries) == len(requests) == audit["requests_sent"] or summary["client_requests"] < len(requests):
        raise ValueError("Request/reservation counts disagree")
    total, unknown = Decimal(0), 0
    for request,entry in zip(requests,entries):
        if request["reservation_id"] != entry["reservation_id"]:
            raise ValueError("Reservation identity mismatch")
        reserve = ((Decimal(request["input_bytes"])+Decimal(policy["token_overhead_reserve"]))
            *Decimal(policy["input_rate_cny_per_million"])
            +Decimal(policy["max_tokens"]+policy["thinking_budget"])*Decimal(policy["output_rate_cny_per_million"]))/Decimal(1000000)
        if reserve != Decimal(entry["reserved_cny"]) or reserve != Decimal(request["reserved_estimate_cny"]):
            raise ValueError("Reservation calculation mismatch")
        if entry["estimated_cny"] is None:
            if entry["status"] not in ("reserved","uncertain_reserved"):
                raise ValueError("Unknown fee incorrectly settled")
            total += reserve
            unknown += 1
        else:
            usage = entry["usage"]
            if usage != request["usage"] or usage["total_tokens"] != usage["prompt_tokens"]+usage["completion_tokens"]:
                raise ValueError("Usage mismatch")
            estimate = (Decimal(usage["prompt_tokens"])*Decimal(policy["input_rate_cny_per_million"])
                +Decimal(usage["completion_tokens"])*Decimal(policy["output_rate_cny_per_million"]))/Decimal(1000000)
            if estimate != Decimal(entry["estimated_cny"]) or estimate != Decimal(request["usage_cost_upper_estimate_cny"]):
                raise ValueError("Usage estimate mismatch")
            total += estimate
    if total != Decimal(summary["estimated_committed_cny"]) or total != Decimal(audit["ledger"]["run_committed_estimate_cny"]):
        raise ValueError("Run total mismatch")
    if unknown != summary["unknown_cost_requests"] or unknown != audit["ledger"]["unknown_cost_requests"]:
        raise ValueError("Unknown request count mismatch")
    simulated = summary["client_mode"] == "siliconflow_HTTP_MOCK_NOT_REAL_LLM"
    if simulated != audit["simulation_not_real_usage"] or simulated != summary["usage_estimate_is_simulated"]:
        raise ValueError("Simulation provenance mismatch")
    if summary["real_LLM_requests"] != (0 if simulated else len(requests)):
        raise ValueError("Real-request declaration mismatch")
    if simulated:
        with sqlite3.connect((run / "mock-budget.sqlite").resolve().as_uri()+"?mode=ro",uri=True) as db:
            saved = db.execute("SELECT id,reserved_cny,estimated_cny,status FROM reservations ORDER BY rowid").fetchall()
        expected = [(entry["reservation_id"],entry["reserved_cny"],entry["estimated_cny"],entry["status"]) for entry in entries]
        if saved != expected: raise ValueError("Mock ledger disagrees with audit")
    result = {"status":"passed", "summary_sha256":sha256_file(summary_path),
        "checked_requests":len(requests),"real_LLM_requests":summary["real_LLM_requests"],
        "simulation_not_real_usage":simulated,"recomputed_committed_estimate_cny":str(total),
        "unknown_cost_requests":unknown,"limits":"Local estimate verification only; no provider invoice or account balance verified."}
    (run / "usage-verification.json").write_text(json.dumps(result,indent=2)+"\n",encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary",type=Path,required=True)
    print(json.dumps(verify(parser.parse_args().summary),indent=2))
