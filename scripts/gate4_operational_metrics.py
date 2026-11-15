"""Extract supplemental operational metrics without editing frozen scores."""
from collections import Counter
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path


def summarize_slot(slot_dir):
    slot_dir = Path(slot_dir)
    start = json.loads((slot_dir / "start.json").read_bytes())
    result_path = slot_dir / "result.json"
    if not result_path.is_file():
        return dict(slot_index=start["slot_index"], condition=start["slot"]["condition"], status="started_unresolved")
    raw = result_path.read_bytes()
    if json.loads((slot_dir / "result-sha256.json").read_bytes())["sha256"] != sha256(raw).hexdigest():
        raise ValueError("Formal result hash mismatch")
    result = json.loads(raw)
    trajectory = json.loads((slot_dir / "live" / "trajectory.json").read_bytes())
    if result["slot_index"] != start["slot_index"] or result["condition"] != trajectory["condition"]:
        raise ValueError("Formal slot and trajectory mismatch")
    scored = {phase["stage"]: phase["assessment"] for phase in result["phases"]}
    phases = []
    seen_reservations = set()
    for phase in trajectory["phases"]:
        stage = phase["stage"]
        counters = Counter()
        known = Decimal(0)
        statuses = Counter()
        for entry in (phase.get("usage") or {}).get("request_audit", []):
            identity = entry["reservation_id"]
            if identity in seen_reservations:
                raise ValueError("Duplicate model reservation across phases")
            seen_reservations.add(identity)
            usage = entry.get("usage")
            if usage is not None:
                counters.update(prompt_tokens=usage["prompt_tokens"], completion_tokens=usage["completion_tokens"],
                                metered_requests=1)
                known += Decimal(entry["estimated_cny"])
            if entry.get("transport_timeout_type"):
                statuses["transport_timeout_unknown" if usage is None else "transport_timeout_metered"] += 1
            elif entry.get("http_status") not in (None, 200):
                statuses["http_failure"] += 1
            elif usage is None:
                statuses["execution_or_usage_unknown"] += 1
            else:
                statuses["metered_response"] += 1
        queries, seen = Counter(), set()
        trace = (phase.get("plan") or {}).get("trace", [])
        first_unavailable_turn = None
        for item in trace:
            outcomes = item.get("query_outcomes", {})
            turn = item["model_turn"]
            if not item.get("ok", False):
                queries["invalid_or_rejected"] += 1
            for source, status in outcomes.items():
                if source in seen:
                    queries["repeat_source_receipts"] += 1
                elif status == "available" and first_unavailable_turn is not None and turn > first_unavailable_turn:
                    queries["new_available_after_unavailable_in_later_turn"] += 1
                seen.add(source)
            if "unavailable" in outcomes.values() and first_unavailable_turn is None:
                first_unavailable_turn = turn
        evaluation = scored[stage]
        phases.append(dict(stage=stage, delivered=evaluation["delivered"],
            customer_state_correct=evaluation["full_information_target_correct"],
            supported_facts=evaluation["supported_fact_count"], reported_facts=evaluation["fact_count"],
            critical_groups_acquired=evaluation["acquired_critical_evidence_groups"],
            critical_groups=evaluation["critical_evidence_groups"],
            required_counterevidence_present=evaluation["required_counterevidence_present"],
            incorrect_safe_close=evaluation["incorrect_safe_close"], strict=evaluation["strict_author_reference_pass"],
            tool_attempts=phase["source_tool_attempts"], planning_queries=dict(queries),
            model_requests=sum(statuses.values()), request_outcomes=dict(statuses), tokens=dict(counters),
            metered_estimate_cny=str(known), simulated_query_minutes=phase["simulated_source_latency_minutes"],
            end_to_end_seconds=phase["process_elapsed_seconds_including_recovery"],
            metered_but_report_failed=bool(counters["metered_requests"] and not evaluation["delivered"]
                                           and not phase["blocked_uncertain_request"]),
            unknown_stop=phase["blocked_uncertain_request"]))
    if len(seen_reservations) != result["model_requests"]:
        raise ValueError("Phase audit count does not match formal request count")
    return dict(slot_index=result["slot_index"], condition=result["condition"], status="terminal",
                blocked=result["blocked"], protected_cny=result["risk_hold_cny"], phases=phases)


def collect(output, *, planned_slots):
    output = Path(output)
    rows = [summarize_slot(p) for p in sorted(output.glob("slot-*"))]
    indexes = [r["slot_index"] for r in rows]
    if indexes != list(range(len(rows))) or len(rows) > planned_slots:
        raise ValueError("Unexpected formal slot index")
    return dict(scope="supplemental_operational_metrics", planned_slots=planned_slots,
        not_started_slots=planned_slots - len(rows), rows=rows,
        interpretation="Later new original after failure is a sequence marker; hypothesis change requires trace review.")
