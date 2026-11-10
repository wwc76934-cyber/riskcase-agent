"""Frozen slot selection and budget admission for the formal study."""
from collections import Counter
from contextlib import closing
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import sqlite3

from app.evaluation.gate4_candidate import UNKNOWN_HOLD, source_fingerprint
from app.evaluation.v31_query_runner import registered_plan
from app.investigation.contracts import digest
from app.llm.cost_ledger import CostLedger


def committed(project_ledger):
    path = Path(project_ledger).resolve()
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        rows = db.execute("SELECT reserved_cny, estimated_cny FROM reservations").fetchall()
        return CostLedger.committed(rows) + CostLedger.holds(db)


def freeze_tranche(dataset, preregistration, envelope, *, start, count, baseline_cny, cap_cny, project_cap_cny):
    protocol, schedule, _ = registered_plan(dataset, preregistration)
    if (envelope["scope"] != "formal_cost_envelope_only" or envelope["live_baselines_compatible"] is not True
            or envelope["external_generation_authorized"] is not False
            or envelope["code_sha256"] != source_fingerprint()
            or envelope["dataset_sha256"] != protocol["dataset_sha256"]
            or envelope["protocol_sha256"] != protocol["protocol_sha256"]
            or envelope["schedule_digest"] != digest(schedule)):
        raise ValueError("Live baseline receipt and unchanged frozen research envelope required")
    model_slots = [row for row in schedule if row["condition"] != "B0"]
    if type(start) is not int or type(count) is not int or start < 0 or count < 1 or start + count > len(model_slots):
        raise ValueError("Tranche must be a contiguous segment of the frozen model schedule")
    bounds = {arm: 3 * cap for arm, cap in protocol["max_per_stage_requests"].items()}
    unit = Decimal(envelope["request_cny_bound"])
    cap, baseline, project_cap = map(Decimal, (cap_cny, baseline_cny, project_cap_cny))
    if not all(v.is_finite() and v >= 0 for v in (cap, baseline, project_cap)):
        raise ValueError("Finite approved budgets required")
    slots = model_slots[start:start + count]
    largest = unit * max(bounds[row["condition"]] for row in slots) + UNKNOWN_HOLD
    if cap < largest or baseline + cap > project_cap:
        raise ValueError("Approved cap must fund any one scheduled trajectory plus unknown protection")
    counts = Counter(row["condition"] for row in slots)
    maximum_requests = sum(bounds[c] * n for c, n in counts.items())
    return dict(scope="gate4_formal_tranche_pending_approval", envelope_digest=digest(envelope),
        dataset_sha256=protocol["dataset_sha256"], protocol_sha256=protocol["protocol_sha256"],
        schedule_digest=digest(schedule), start_model_slot=start, planned_model_slots=slots,
        planned_conditions=dict(counts), request_caps_by_trajectory=bounds, max_requests=maximum_requests,
        baseline_cny=str(baseline), cap_cny=str(cap), project_cap_cny=str(project_cap),
        largest_next_slot_reserve_cny=str(largest),
        full_tranche_usage_upper_cny=str(unit * maximum_requests + UNKNOWN_HOLD),
        budget_stop_preserves_denominator=True, external_generation_authorized=False)


def next_slot_admission(tranche, envelope, index, project_committed):
    slots = tranche["planned_model_slots"]
    if type(index) is not int or not 0 <= index < len(slots):
        raise ValueError("Unregistered tranche index")
    arm = slots[index]["condition"]
    required = Decimal(envelope["request_cny_bound"]) * tranche["request_caps_by_trajectory"][arm] + UNKNOWN_HOLD
    committed_now = Decimal(project_committed)
    baseline = Decimal(tranche["baseline_cny"])
    ceiling = min(Decimal(tranche["project_cap_cny"]), baseline + Decimal(tranche["cap_cny"]))
    if not committed_now.is_finite() or committed_now < baseline:
        raise ValueError("Historical project commitments disappeared")
    return dict(allowed=committed_now + required <= ceiling, required_cny=str(required),
                available_cny=str(max(Decimal(0), ceiling - committed_now)))

def phase_rows(snapshot, reference, trajectory):
    from app.evaluation.v31_query_score import score_stage

    rows = []
    for phase in trajectory["phases"]:
        plan = phase.get("plan") or {}
        trace = plan.get("trace", [])
        rows.append(dict(stage=phase["stage"],
            assessment=score_stage(snapshot, reference, phase["stage"], phase["evidence"], phase["report"]),
            source_tool_attempts=phase["source_tool_attempts"], model_plan_tool_attempts=len(trace),
            rejected_plan_tool_attempts=sum(not step.get("ok", False) for step in trace),
            model_plan_turns=plan.get("model_requests", 0), query_trace=trace,
            simulated_query_minutes=phase["simulated_source_latency_minutes"],
            elapsed_seconds=phase["process_elapsed_seconds_including_recovery"],
            safe_stop=phase["blocked_uncertain_request"], error=phase["error"]))
    return rows


def _read_result(folder):
    raw = (folder / "result.json").read_bytes()
    if json.loads((folder / "result-sha256.json").read_bytes())["sha256"] != sha256(raw).hexdigest():
        raise ValueError("Formal result fingerprint changed")
    return json.loads(raw)


def _completed_prefix(output, envelope_digest):
    """Any unfinished or unsafe started slot fences subsequent dispatch."""
    previous = sorted(output.glob("slot-*"))
    for index, folder in enumerate(previous):
        if folder.name != f"slot-{index:04}":
            raise ValueError("Noncontiguous formal result folders")
        terminal = folder / "result.json"
        if not terminal.is_file():
            raise ValueError("An earlier formal slot is unresolved")
        record = _read_result(folder)
        if record["execution_binding"] != envelope_digest or record["slot_index"] != index or record["blocked"]:
            raise ValueError("An earlier formal slot needs independent review")
    return len(previous)


def execute_tranche(dataset, preregistration, envelope_path, tranche_path, grant_path, project_ledger, output):
    """Run approved contiguous slots and stop before a slot without headroom."""
    import asyncio
    import httpx
    from app.evaluation.gate4_candidate import CandidateBudget, run_candidate
    from app.evaluation.v3_tasks import write_new
    from app.investigation.v3.gate4_provider import Gate4Policy
    from app.llm.credentials import load_key

    envelope = json.loads(Path(envelope_path).read_bytes())
    raw = Path(tranche_path).read_bytes()
    tranche, grant = json.loads(raw), json.loads(Path(grant_path).read_bytes())
    grant_binding = sha256(raw).hexdigest()
    start = tranche["start_model_slot"]
    verified = freeze_tranche(dataset, preregistration, envelope, start=start,
        count=len(tranche["planned_model_slots"]), baseline_cny=tranche["baseline_cny"],
        cap_cny=tranche["cap_cny"], project_cap_cny=tranche["project_cap_cny"])
    if (tranche != verified or grant.get("state") != "approved" or grant.get("tranche_sha256") != grant_binding
            or grant.get("cap_cny") != tranche["cap_cny"] or grant.get("project_cap_cny") != tranche["project_cap_cny"]
            or not grant.get("user_message")):
        raise ValueError("Explicit new formal approval required")

    ledger, output = Path(project_ledger), Path(output)
    if not ledger.is_file() or (key := load_key()) is None:
        raise ValueError("Existing project ledger and secure local credentials required")
    baseline = Decimal(tranche["baseline_cny"])
    effective_cap = min(Decimal(tranche["project_cap_cny"]), baseline + Decimal(tranche["cap_cny"]))
    if committed(ledger) < baseline:
        raise ValueError("Historical project commitments disappeared")
    execution_binding = digest(envelope)
    output.mkdir(parents=True, exist_ok=True)
    if _completed_prefix(output, execution_binding) != start:
        raise ValueError("New tranche must start at the next unstarted slot")
    batch = output / ("tranche-" + grant_binding)
    batch.mkdir()
    write_new(batch / "start.json", dict(tranche_sha256=grant_binding, execution_binding=execution_binding))
    policy = Gate4Policy(**envelope["policy"])
    protocol, _, cases = registered_plan(dataset, preregistration)
    reason = "all_tranche_slots_finished"
    completed = 0
    with httpx.Client(transport=httpx.HTTPTransport(retries=0), follow_redirects=False, trust_env=False) as http:
        for local_index, slot in enumerate(tranche["planned_model_slots"]):
            admission = next_slot_admission(tranche, envelope, local_index, committed(ledger))
            if not admission["allowed"]:
                reason = "no_room_for_next_full_trajectory"
                break
            index = start + local_index
            case, arm = slot["case_id"], slot["condition"]
            folder = output / f"slot-{index:04}"
            folder.mkdir()  # Exclusive claim before any provider call.
            write_new(folder / "start.json", dict(slot=slot, slot_index=index, execution_binding=execution_binding))
            _, snapshot, reference = cases[case]
            bound = tranche["request_caps_by_trajectory"][arm]
            budget = CandidateBudget(ledger, policy=policy, scope=digest([execution_binding, index]),
                baseline=baseline, cap=admission["required_cny"], request_limit=bound, project_cap=effective_cap)
            budget.start()
            result = asyncio.run(run_candidate(folder / "live", snapshot, policy, budget, http, key,
                evidence_kind="separately_authorized_external_client", condition=arm))
            financial = budget.summary(budget.prefix)
            amounts = financial["entries"]
            known = sum((Decimal(item["estimated_cny"]) for item in amounts if item["estimated_cny"] is not None), Decimal(0))
            holds = Decimal(financial["run_committed_estimate_cny"]) - known
            blocked = result["blocked_uncertain_request"] or financial["unknown_cost_requests"] > 0
            row = dict(slot, slot_index=index, execution_binding=execution_binding,
                dataset_sha256=protocol["dataset_sha256"], protocol_sha256=protocol["protocol_sha256"],
                evidence_kind="formal_provider_run", phases=phase_rows(snapshot, reference, result),
                model_requests=len(amounts), external_model_requests=len(amounts),
                estimated_cny=str(known), risk_hold_cny=str(holds), blocked=blocked)
            write_new(folder / "result.json", row)
            write_new(folder / "result-sha256.json", dict(sha256=sha256((folder / "result.json").read_bytes()).hexdigest()))
            completed += 1
            if blocked:
                reason = "uncertain_execution_or_fees"
                break
    summary = dict(scope="formal_simulation_tranche", stop_reason=reason, started_slots=completed,
        planned_slots=len(tranche["planned_model_slots"]), first_slot=start, execution_binding=execution_binding,
        project_committed_cny=str(committed(ledger)), preregistered_model_slots=envelope["planned_condition_runs"])
    write_new(batch / "summary.json", summary)
    return summary


def summarize_execution(dataset, preregistration, envelope, output):
    """Read formal artifacts and obtain deterministic B0 with the same MCP source."""
    import asyncio
    from app.evaluation.gate4_candidate import run_candidate
    from app.evaluation.v3_tasks import write_new
    from app.evaluation.v31_query_runner import summarize_registered

    protocol, schedule, cases = registered_plan(dataset, preregistration)
    if (envelope["scope"] != "formal_cost_envelope_only" or envelope["live_baselines_compatible"] is not True
            or envelope["dataset_sha256"] != protocol["dataset_sha256"]
            or envelope["protocol_sha256"] != protocol["protocol_sha256"]
            or envelope["schedule_digest"] != digest(schedule)):
        raise ValueError("Formal execution artifacts do not match the frozen study")
    output = Path(output)
    slots = [row for row in schedule if row["condition"] != "B0"]
    binding = digest(envelope)
    rows = []
    for index, folder in enumerate(sorted(output.glob("slot-*"))):
        if folder.name != f"slot-{index:04}" or index >= len(slots):
            raise ValueError("Unexpected formal result folder")
        artifact = folder / "result.json"
        if not artifact.is_file():
            continue  # Unfinished attempts stay missing, in the original denominator.
        row = _read_result(folder)
        if (row["execution_binding"] != binding or row["slot_index"] != index
                or row["evidence_kind"] != "formal_provider_run"
                or any(row[key] != value for key, value in slots[index].items())
                or row["external_model_requests"] != row["model_requests"]):
            raise ValueError("Formal model evidence does not match its slot")
        rows.append(row)
    if not rows:
        raise ValueError("Formal provider evidence is required before comparison")
    baseline_root = output / "deterministic-B0"
    baseline_root.mkdir(exist_ok=True)
    for case, (_, snapshot, reference) in cases.items():
        artifact = baseline_root / (case + ".json")
        if not artifact.exists():
            result = asyncio.run(run_candidate(baseline_root / case, snapshot, None, None, None, None,
                evidence_kind="scripted_protocol_probe", condition="B0"))
            write_new(artifact, dict(case_id=case, condition="B0", repeat=1,
                execution_binding=binding, dataset_sha256=protocol["dataset_sha256"],
                protocol_sha256=protocol["protocol_sha256"], evidence_kind="formal_deterministic_baseline",
                phases=phase_rows(snapshot, reference, result), model_requests=0,
                external_model_requests=0, estimated_cny="0", risk_hold_cny="0"))
        row = json.loads(artifact.read_bytes())
        if row["execution_binding"] != binding or row["case_id"] != case or row["condition"] != "B0":
            raise ValueError("Deterministic baseline artifact changed scope")
        rows.append(row)
    report = summarize_registered(dataset, preregistration, rows, scope="formal_simulation_comparison")
    report["execution_binding"] = binding
    report["review_scope"] = "AI_author_reviewed_simulation"
    for arm, stats in report["conditions"].items():
        phases = [p for row in rows if row["condition"] == arm for p in row["phases"]]
        stats.update(model_plan_turns=sum(p["model_plan_turns"] for p in phases),
            model_plan_tool_attempts=sum(p["model_plan_tool_attempts"] for p in phases),
            rejected_plan_tool_attempts=sum(p["rejected_plan_tool_attempts"] for p in phases),
            blocked_stages=sum(p["safe_stop"] for p in phases))
    return report
