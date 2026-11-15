"""Qwen mechanism audit for the two non-formal development cases."""
import asyncio
from dataclasses import asdict
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path

import httpx

from app.evaluation.gate4_candidate import CandidateBudget, UNKNOWN_HOLD, run_candidate, source_fingerprint
from app.evaluation.gate4_formal_tranche import committed
from app.evaluation.v31_query_development import callback_and_capture
from app.evaluation.v31_query_runner import registered_plan
from app.evaluation.v3_tasks import write_new
from app.investigation.contracts import digest
from app.investigation.v3.gate4_provider import Gate4Policy
from app.llm.credentials import load_key


ARMS = {"A": True, "B": False}
REQUESTS_PER_CASE = 18


def prepare(dataset, preregistration, baseline):
    protocol, _, formal = registered_plan(dataset, preregistration)
    policy = Gate4Policy("siliconflow-cn", "Qwen/Qwen3.8-27B", "json_schema", "3", "12",
        "https://www.siliconflow.cn/pricing", "2026-11-10",
        timeout_seconds=90, expand_hypothesis_branches=True)
    cases = {}
    for arm, available in ARMS.items():
        snapshot, reference = callback_and_capture(callback_available=available)
        if snapshot["case_id"] in formal or any(
                snapshot["subject_id"] == row[1]["subject_id"] for row in formal.values()):
            raise ValueError("Mechanism audit must be outside the formal fold")
        cases[arm] = dict(case_id=snapshot["case_id"], runtime_sha256=digest(snapshot),
                         reference_sha256=digest(reference))
    unit = policy.estimate(policy.max_input_bytes + 4096, policy.max_output_tokens)
    cap = unit * REQUESTS_PER_CASE * len(ARMS) + UNKNOWN_HOLD
    current = Decimal(baseline)
    if not current.is_finite() or current < 0 or current + cap > 42:
        raise ValueError("Insufficient headroom under the existing 42 CNY project cap")
    return dict(scope="gate4_development_mechanism_only", cases=cases,
        formal_protocol_sha256=protocol["protocol_sha256"], code_sha256=source_fingerprint(),
        audit_code_sha256=sha256(Path(__file__).read_bytes()).hexdigest(),
        policy=asdict(policy), max_requests=REQUESTS_PER_CASE * len(ARMS),
        baseline_cny=str(current), cap_cny=str(cap), project_cap_cny="42")


def inspect(trajectory, reference, *, primary_available):
    primary, secondary = reference["required_T1_queries"]
    first_status = "available" if primary_available else "unavailable"
    stages = []
    for phase in trajectory["phases"]:
        seen = set()
        steps = []
        trace = (phase.get("plan") or {}).get("trace", [])
        for step in trace:
            outcomes = step.get("query_outcomes", {})
            newly_reported = sorted(set(outcomes) - seen)
            seen.update(outcomes)
            steps.append(dict(step, newly_reported_source_ids=newly_reported))
        stages.append(dict(stage=phase["stage"], delivered=phase["report"] is not None,
            query_trace=steps, error=phase["error"], simulated_query_minutes=phase["simulated_source_latency_minutes"],
            total_seconds=phase["process_elapsed_seconds_including_recovery"],
            provider_seconds=sum(r["elapsed_seconds"] for r in (phase.get("usage") or {}).get("request_audit", []))))
    trace = next((s["query_trace"] for s in stages if s["stage"] == 1), [])
    first_turns = [step["model_turn"] for step in trace if step.get("query_outcomes", {}).get(primary) == first_status]
    second_turns = [step["model_turn"] for step in trace if step.get("query_outcomes", {}).get(secondary) == "available"]
    return dict(stages=stages,
        primary_receipt_observed=bool(first_turns), necessary_secondary_acquired=bool(second_turns),
        same_model_turn=bool(set(first_turns) & set(second_turns)),
        secondary_first_obtained_after_feedback=bool(first_turns and second_turns and min(first_turns) < min(second_turns)),
        rejected_queries=sum(not step.get("ok", False) for stage in stages for step in stage["query_trace"]),
        blocked=trajectory["blocked_uncertain_request"],
        rubric="Turn order establishes sequence. Hypothesis text and stopping reasons require review; same-turn reads do not establish feedback.")


def execute(dataset, preregistration, proposal, grant_path, ledger_path, output):
    raw = Path(proposal).read_bytes()
    plan, grant = json.loads(raw), json.loads(Path(grant_path).read_bytes())
    binding = sha256(raw).hexdigest()
    if (plan != prepare(dataset, preregistration, plan["baseline_cny"])
            or grant.get("state") != "approved" or grant.get("plan_sha256") != binding
            or grant.get("cap_cny") != plan["cap_cny"] or grant.get("project_cap_cny") != "42"
            or not grant.get("user_message")):
        raise ValueError("Explicit separate mechanism audit authorization required")
    ledger_path, output = Path(ledger_path), Path(output)
    if not ledger_path.is_file() or str(committed(ledger_path)) != plan["baseline_cny"] or not (key := load_key()):
        raise ValueError("Expected original ledger balance and secure credential")
    policy = Gate4Policy(**plan["policy"])
    case_bound = policy.estimate(policy.max_input_bytes + 4096, policy.max_output_tokens) * REQUESTS_PER_CASE + UNKNOWN_HOLD
    ceiling = Decimal(plan["baseline_cny"]) + Decimal(plan["cap_cny"])
    output.mkdir(parents=True, exist_ok=False)
    results = {}
    with httpx.Client(transport=httpx.HTTPTransport(retries=0), follow_redirects=False, trust_env=False) as http:
        for arm, available in ARMS.items():
            snapshot, reference = callback_and_capture(callback_available=available)
            budget = CandidateBudget(ledger_path, policy=policy, scope=digest([binding, arm]),
                baseline=plan["baseline_cny"], cap=case_bound, request_limit=REQUESTS_PER_CASE,
                project_cap=ceiling)
            budget.start()
            trajectory = asyncio.run(run_candidate(output / arm, snapshot, policy, budget, http, key,
                evidence_kind="separately_authorized_external_client"))
            row = inspect(trajectory, reference, primary_available=available)
            finance = budget.summary(budget.prefix)
            row["requests"] = len(finance["entries"])
            row["known_and_reserved_cny"] = finance["run_committed_estimate_cny"]
            row["unknown_cost_requests"] = finance["unknown_cost_requests"]
            write_new(output / arm / "audit.json", row)
            results[arm] = row
            if row["blocked"] or row["unknown_cost_requests"]:
                break
    write_new(output / "summary.json", dict(scope=plan["scope"], planned=list(ARMS), results=results,
        project_committed_cny=str(committed(ledger_path))))
