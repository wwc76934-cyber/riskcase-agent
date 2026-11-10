"""Prepare an offline cost envelope for the preregistered four-condition study."""
from collections import Counter
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path

from app.evaluation.gate4_baseline_check import CONDITIONS as CHECKED_CONDITIONS
from app.evaluation.gate4_candidate import UNKNOWN_HOLD, source_fingerprint
from app.evaluation.v31_query_runner import registered_plan
from app.investigation.v3.gate4_provider import Gate4Policy


def formal_envelope(dataset, preregistration, wire_receipt, baseline_plan, *, compatibility_receipt=None):
    protocol, schedule, cases = registered_plan(dataset, preregistration)
    wire_path = Path(wire_receipt)
    wire = json.loads(wire_path.read_bytes())
    policy = Gate4Policy(**baseline_plan["policy"])
    model_conditions = tuple(c for c in protocol["conditions"] if c != "B0")
    if (wire["scope"] != "offline_wire_audit_only" or wire["external_model_requests"] != 0
            or wire["dataset_sha256"] != protocol["dataset_sha256"]
            or wire["protocol_sha256"] != protocol["protocol_sha256"]
            or wire["schedule_slots"] != len(schedule) or wire["model_binding"] != policy.binding
            or wire["common_limit_under_test"] != policy.max_input_bytes):
        raise ValueError("Wire receipt does not bind this frozen study and policy")
    seen = set()
    measured = {condition: dict(requests=0, maximum_bytes=0) for condition in model_conditions}
    for row in wire["rows"]:
        case, condition = row["case_id"], row["condition"]
        slot = case, condition
        if slot in seen or case not in cases or condition not in model_conditions:
            raise ValueError("Unexpected wire probe case or condition")
        seen.add(slot)
        if row["delivered"] != 3 or row["errors"]:
            raise ValueError("Unresolved offline delivery probe")
        counts = Counter()
        for stage, lane, size in row["requests"]:
            if (type(stage) is not int or stage not in (0, 1, 2)
                    or lane not in ("planner", "extract", "report", "extract_and_report")
                    or type(size) is not int or not 0 < size <= policy.max_input_bytes):
                raise ValueError("Wire probe exceeds the frozen transport envelope")
            counts[stage] += 1
            entry = measured[condition]
            entry["requests"] += 1
            entry["maximum_bytes"] = max(entry["maximum_bytes"], size)
        if any(count > protocol["max_per_stage_requests"][condition] for count in counts.values()):
            raise ValueError("Wire probe exceeds registered per-stage requests")
    if seen != {(case, condition) for case in cases for condition in model_conditions}:
        raise ValueError("Expected all 40 cases on each model route")

    compatible, receipt_hash = False, None
    if compatibility_receipt is not None:
        raw = Path(compatibility_receipt).read_bytes()
        live = json.loads(raw)
        compatible = (live["scope"] == "development_compatibility"
            and live["protocol_compatible"] is True
            and set(live["results"]) == set(CHECKED_CONDITIONS)
            and all(row["evidence_kind"] == "separately_authorized_external_client"
                    and row["complete_delivery"] and not row["blocked_uncertain_request"]
                    for row in live["results"].values()))
        receipt_hash = sha256(raw).hexdigest()

    planned = Counter(row["condition"] for row in schedule)
    requests = {c: planned[c] * 3 * protocol["max_per_stage_requests"][c] for c in model_conditions}
    total_requests = sum(requests.values())
    unit_bound = policy.estimate(policy.max_input_bytes + 4096, policy.max_output_tokens)
    known_bound = total_requests * unit_bound
    return dict(scope="formal_cost_envelope_only", external_generation_authorized=False,
        dataset_sha256=protocol["dataset_sha256"], protocol_sha256=protocol["protocol_sha256"],
        schedule_digest=protocol["schedule_digest"], code_sha256=source_fingerprint(),
        wire_receipt_sha256=sha256(wire_path.read_bytes()).hexdigest(),
        compatibility_receipt_sha256=receipt_hash, live_baselines_compatible=compatible,
        policy=baseline_plan["policy"], planned_condition_runs=dict(planned), max_requests_by_condition=requests,
        max_external_requests=total_requests, measured_offline=measured,
        per_request_input_token_proxy=policy.max_input_bytes + 4096,
        max_output_tokens=policy.max_output_tokens, request_cny_bound=str(unit_bound),
        planned_usage_upper_cny=str(known_bound), first_unknown_hold_cny=str(UNKNOWN_HOLD),
        batch_upper_with_first_unknown_cny=str(known_bound + UNKNOWN_HOLD),
        price_model="uncached_text_and_output_local_conservative_reservation")
