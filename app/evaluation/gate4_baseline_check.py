"""Separately approved B1/B4 compatibility check before the formal study."""
import asyncio
from dataclasses import asdict
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path

import httpx

from app.evaluation.gate4_candidate import (
    CandidateBudget, MAX_COMPATIBILITY_REQUESTS, UNKNOWN_HOLD, run_candidate,
    source_fingerprint, summarize_candidate,
)
from app.evaluation.v31_query_development import callback_and_capture
from app.investigation.contracts import digest
from app.investigation.v3.gate4_provider import Gate4Policy
from app.llm.credentials import load_key
from app.service.runtime import write_json


CONDITIONS = ("B1", "B4")


def prepare(development, baseline, project_cap):
    development = Path(development)
    dev_plan = json.loads((development / "proposal.json").read_bytes())
    receipt = (development / "live/summary.json").read_bytes()
    summary = json.loads(receipt)
    snapshot, reference = callback_and_capture(callback_available=False)
    policy = Gate4Policy(**dict(dev_plan["policy"], max_input_bytes=64000))
    if (summary["development_qualification_pass"] is not True
            or summary["evidence_kind"] != "separately_authorized_external_client"
            or dev_plan["runtime_sha256"] != digest(snapshot)
            or dev_plan["reference_sha256"] != digest(reference)
            or policy.model != "Qwen/Qwen3.8-27B"):
        raise ValueError("Qualified Qwen development evidence required")
    amount = policy.estimate(policy.max_input_bytes + 4096, policy.max_output_tokens)
    cap = amount * MAX_COMPATIBILITY_REQUESTS + UNKNOWN_HOLD
    baseline, project_cap = Decimal(baseline), Decimal(project_cap)
    if not baseline.is_finite() or not project_cap.is_finite() or baseline + cap > project_cap:
        raise ValueError("Insufficient headroom for compatibility checks")
    return dict(contract="gate4-baseline-compatibility-v1", conditions=list(CONDITIONS),
        policy=asdict(policy), policy_binding=policy.binding, code_sha256=source_fingerprint(),
        development_receipt_sha256=sha256(receipt).hexdigest(), runtime_sha256=digest(snapshot),
        reference_sha256=digest(reference), baseline_cny=str(baseline), cap_cny=str(cap),
        project_cap_cny=str(project_cap), max_requests=MAX_COMPATIBILITY_REQUESTS)


def execute(development, plan_file, grant_file, ledger_path, output):
    raw = Path(plan_file).read_bytes()
    plan, grant = json.loads(raw), json.loads(Path(grant_file).read_bytes())
    scope = sha256(raw).hexdigest()
    if (plan != prepare(development, plan["baseline_cny"], plan["project_cap_cny"])
            or grant.get("state") != "approved" or grant.get("plan_sha256") != scope
            or grant.get("cap_cny") != plan["cap_cny"] or grant.get("project_cap_cny") != plan["project_cap_cny"]
            or not grant.get("user_message")):
        raise ValueError("Separate compatibility authorization required")
    policy = Gate4Policy(**plan["policy"])
    output, ledger_path = Path(output), Path(ledger_path)
    key = load_key()
    if not key or output.exists() or not ledger_path.is_file():
        raise ValueError("Project key, existing ledger and fresh output required")
    budget = CandidateBudget(ledger_path, policy=policy, scope=scope, baseline=plan["baseline_cny"],
        cap=plan["cap_cny"], request_limit=plan["max_requests"], project_cap=plan["project_cap_cny"])
    snapshot, reference = callback_and_capture(callback_available=False)
    budget.start()
    output.mkdir()
    results = {}
    previous_count = 0
    previous_commitment = Decimal(0)
    with httpx.Client(transport=httpx.HTTPTransport(retries=0), follow_redirects=False, trust_env=False) as http:
        for condition in CONDITIONS:
            trajectory = asyncio.run(run_candidate(output / condition, snapshot, policy, budget, http, key,
                evidence_kind="separately_authorized_external_client", condition=condition))
            financial = budget.summary(budget.prefix)
            summary = summarize_candidate(snapshot, reference, trajectory, financial)
            count = summary["model_requests"]
            commitment = Decimal(summary["estimated_and_reserved_cny"])
            summary["model_requests"] = count - previous_count
            summary["estimated_and_reserved_cny"] = str(commitment - previous_commitment)
            previous_count, previous_commitment = count, commitment
            write_json(output / condition / "summary.json", summary)
            results[condition] = summary
            if summary["blocked_uncertain_request"]:
                break
    receipt = dict(scope="development_compatibility", planned_conditions=list(CONDITIONS),
        results=results, protocol_compatible=len(results) == len(CONDITIONS) and all(
            row["complete_delivery"] and not row["blocked_uncertain_request"] for row in results.values()))
    write_json(output / "summary.json", receipt)
    return receipt
