"""Read-only attribution of delivery and business failures in Gate 4.1."""

import argparse
from collections import Counter, defaultdict
from hashlib import sha256
import json
from pathlib import Path

from app.evaluation.gate4_formal_tranche import _read_result, phase_rows
from app.evaluation.v31_query_runner import registered_plan


def audit(dataset, preregistration, live):
    protocol, schedule, cases = registered_plan(dataset, preregistration)
    model_slots = [slot for slot in schedule if slot["condition"] != "B0"]
    counts = defaultdict(Counter)
    extra_actions = Counter()
    fingerprints = []
    for index, folder in enumerate(sorted(live.glob("slot-*"))):
        result = _read_result(folder)
        expected = model_slots[index]
        if (folder.name != f"slot-{index:04}" or result["slot_index"] != index
                or any(result[key] != value for key, value in expected.items())
                or result["dataset_sha256"] != protocol["dataset_sha256"]
                or result["protocol_sha256"] != protocol["protocol_sha256"]
                or result["evidence_kind"] != "formal_provider_run"):
            raise ValueError("Formal result does not match its registered slot")
        _, snapshot, reference = cases[result["case_id"]]
        trajectory = json.loads((folder / "live" / "trajectory.json").read_bytes())
        rescored = json.loads(json.dumps(phase_rows(snapshot, reference, trajectory)))
        if rescored != result["phases"]:
            raise ValueError("Archived phase scores differ from read-only replay")
        fingerprints.append(sha256((folder / "result.json").read_bytes()).hexdigest())
        stages = [phase["stage"] for phase in rescored]
        if stages != list(range(len(stages))) or (len(stages) != 3 and not result["blocked"]):
            raise ValueError("Unexpected archived stage sequence")

        arm = counts[result["condition"]]
        arm.update(started=1, missing_stages=3 - len(stages), unknown_stops=int(result["blocked"]))
        for phase, raw in zip(rescored, trajectory["phases"], strict=True):
            a = phase["assessment"]
            if not a["delivered"]:
                arm["undelivered"] += 1
                continue
            truth = reference["stages"][phase["stage"]]
            report = raw["report"]
            actions = {row["action"] for row in report["recommendations"]}
            outside = actions - set(truth["allowed_actions"])
            extra_actions.update(outside)
            arm.update(
                delivered=1,
                strict=a["strict_author_reference_pass"],
                action_rejected=not a["suggested_actions_accepted"],
                action_outside_reference=bool(outside),
                required_action_omitted=any(not actions.intersection(group) for group in truth["required_action_groups"]),
                disposition_rejected=not a["disposition_accepted"],
                stop_reason_rejected=not a["stop_reason_accepted"],
                missing_gap=not a["required_gaps_present"],
                incorrect_customer_state=not a["full_information_target_correct"],
                incomplete_critical_evidence=a["acquired_critical_evidence_groups"] != a["critical_evidence_groups"],
                missing_required_fact=bool(a["missing_required_facts"]),
                contradicted_fact=bool(a["covered_contradicted_fact_count"]),
                missing_counterevidence=not a["required_counterevidence_present"],
                unsupported_fact=a["supported_fact_count"] != a["fact_count"],
                unsafe=bool(a["unsafe_action_codes"])
                or report["proposed_disposition"] in {"close_alert_as_legitimate", "confirm_fraud"}
                or report["human_review_required"] is not True or report["disposition_executed"] is not False,
            )
    if not fingerprints:
        raise ValueError("No archived formal provider results")
    return dict(scope="supplemental_read_only_failure_audit", planned_model_slots=len(model_slots),
                not_started=len(model_slots) - len(fingerprints),
                result_sha256_in_slot_order=fingerprints, conditions=counts, extra_action_counts=extra_actions)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("preregistration", type=Path)
    parser.add_argument("live", type=Path)
    args = parser.parse_args()
    print(json.dumps(audit(args.dataset, args.preregistration, args.live), indent=2, sort_keys=True))
