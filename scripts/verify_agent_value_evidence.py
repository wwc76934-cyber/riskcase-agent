"""Check portable totals and hashes only; no model calls or business certification."""
from __future__ import annotations

import argparse
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from statistics import mean


def require(condition, message):
    if not condition:
        raise ValueError(message)


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def verify_pack(directory):
    directory = Path(directory).resolve()
    manifest = load(directory / "manifest.json")
    require(manifest["schema"] == "agent-value-portable-v1", "Unsupported pack schema")
    files = manifest["artifact_sha256"]
    require({"results.json", "analysis.json", "provenance.json", "recovery.json", "ui-acceptance.json",
             "execution-limitations.json", "demo-trace.json", "post-experiment-maintenance.json"} <= files.keys(),
            "Required evidence artifacts are missing")
    for name, expected in files.items():
        require(name and name not in {".", ".."} and not any(c in name for c in "/\\:"),
                "Artifact name is not local to the pack")
        path = directory / name
        require(path.resolve().parent == directory, "Artifact escapes pack directory")
        require(hashlib.sha256(path.read_bytes()).hexdigest() == expected, "Artifact changed: " + name)

    analysis = load(directory / "analysis.json")
    provenance = load(directory / "provenance.json")
    data = load(directory / "results.json")
    require(analysis["status"] == "complete_internal_simulation_comparison" and not analysis["missing_case_conditions"],
            "Comparison is not complete")
    require(analysis["author_reference_not_external_expert"] is True
            and analysis["real_business_benefit_validated"] is False,
            "Unsupported external validation or business-benefit claim")
    require(provenance["raw_transactions_uploaded"] is False
            and provenance["provider_authenticity_independently_verified"] is False
            and provenance["hidden_reference_visible_to_runtime"] is False,
            "Unexpected provenance declaration")

    rows = data["rows"]
    indexed = {(row["case_id"], row["condition"]): row for row in rows}
    require(len(indexed) == len(rows), "Duplicate case/condition result")
    expected = {(f"world-{n:03d}", c) for n in range(31, 91) for c in ("B0", "B1", "B2", "B3")}
    expected |= {(f"world-{n:03d}", c) for n in range(71, 91) for c in ("raw_history", "stale", "wrong")}
    require(indexed.keys() == expected, "The 300 frozen case/condition results are incomplete or out of scope")
    for (case, condition), row in indexed.items():
        require(row["fold"] == ("frozen" if int(case[-3:]) < 71 else "challenge"), "Fold disagrees with frozen case set")
        assessment = row["assessment"]
        require(0 <= assessment["quality"] <= 1, "Quality is out of range")
        require(not assessment["strict_reference_passed"] or assessment["host_accepted"],
                "Strict pass lacks an accepted report")
        if not assessment["host_accepted"]:
            require(assessment["quality"] == 0 and not assessment["strict_reference_passed"],
                    "Unaccepted report removed from the failure denominator")
        require(row["state"] not in {"running", "queued"}, "A recorded condition is not terminal")
        require(0 <= row["provider_requests"] <= 6, "Per-case request allowance exceeded")
        require(Decimal(row["committed_estimate_cny"]) >= 0, "Negative case estimate")
        if condition == "B0":
            require(row["provider_requests"] == 0 and Decimal(row["committed_estimate_cny"]) == 0,
                    "Code baseline is incorrectly counted as paid model execution")

    expected_groups = {"frozen:" + c for c in ("B0", "B1", "B2", "B3")}
    expected_groups |= {"challenge:" + c for c in ("B0", "B1", "B2", "B3", "raw_history", "stale", "wrong")}
    require(analysis["groups"].keys() == expected_groups, "A comparison group is missing or out of scope")
    for key, group in analysis["groups"].items():
        fold, condition = key.split(":", 1)
        own = [row for row in rows if row["fold"] == fold and row["condition"] == condition]
        require(group["runs"] == len(own), "Group denominator changed: " + key)
        require(group["host_accepted"] == sum(row["assessment"]["host_accepted"] for row in own),
                "Host acceptance total changed: " + key)
        require(group["strict_reference_passed"] == sum(row["assessment"]["strict_reference_passed"] for row in own),
                "Strict reference total changed: " + key)
        require(abs(group["mean_quality"] - mean(row["assessment"]["quality"] for row in own)) <= 0.000001,
                "Mean quality disagrees with original rows: " + key)
        require(group["unsupported_claims"] == sum(len(row["assessment"]["unsupported_claim_ids"]) for row in own)
                and group["unsafe_typed_draft_actions"] == sum(bool(row["assessment"]["unsafe_draft_actions"]) for row in own),
                "Fact/safety errors were omitted: " + key)
        require(group["model_requests"] == sum(row["provider_requests"] for row in own)
                and Decimal(group["committed_estimate_cny"]) == sum((Decimal(row["committed_estimate_cny"]) for row in own), Decimal(0)),
                "Group request/cost totals disagree: " + key)

    for fold in ("frozen", "challenge"):
        for left, right in (("B1", "B0"), ("B2", "B1"), ("B3", "B2"), ("B3", "B1")):
            pair = analysis["paired_quality"][fold + ":" + left + "-" + right]
            cases = [case for case, condition in indexed if condition == left and indexed[(case, condition)]["fold"] == fold]
            delta = mean(indexed[(case, left)]["assessment"]["quality"] - indexed[(case, right)]["assessment"]["quality"] for case in cases)
            require(pair["pairs"] == len(cases) and abs(pair["mean_delta_quality"] - delta) <= 0.000001,
                    "Paired difference disagrees with rows")

    budget = analysis["budget"]
    require(Decimal(budget["max_upgrade_cny"]) == 13 and Decimal(budget["max_project_cny"]) == 28,
            "Recorded authorization limits changed")
    require(budget["registered_case_slots"] == 264 and budget["provider_requests"] <= 1584,
            "Actual case/request counts disagree with complete authorized round")
    require(budget["in_flight_requests"] == 0 and Decimal(budget["in_flight_reservation_cny"]) == 0,
            "Requests are still in flight")
    parts = sum((Decimal(budget[name]) for name in ("known_usage_estimate_cny", "unknown_reservation_cny", "additional_risk_hold_cny")), Decimal(0))
    require(parts == Decimal(budget["upgrade_committed_estimate_cny"]), "Unknown charges or holds omitted from round total")
    require(parts <= 13 and Decimal(budget["project_committed_estimate_cny"]) <= 28, "Authorized budget exceeded")
    require(Decimal(provenance["project_estimate_before_round_cny"]) + parts == Decimal(budget["project_committed_estimate_cny"]),
            "Shared project budget does not reconcile")
    require(budget["provider_bill_verified"] is False, "Local estimates are not verified provider bills")
    development = data["development_budget"]
    require(development["registered_case_slots"] == 24
            and development["provider_requests"] + sum(row["provider_requests"] for row in rows) == budget["provider_requests"],
            "Development requests are missing or counted twice")
    require(Decimal(development["committed_estimate_cny"]) + sum((Decimal(row["committed_estimate_cny"]) for row in rows), Decimal(0)) == parts,
            "Development and formal case costs do not reconcile")

    execution = load(directory / "execution-limitations.json")
    zero_calls = [dict(case_id=row["case_id"], condition=row["condition"], state=row["state"], reason=row["reason"])
                  for row in rows if row["condition"] != "B0" and row["provider_requests"] == 0]
    require(execution["zero_request_non_B0_conditions"] == zero_calls,
            "Conditions blocked before a provider request were concealed")
    require(execution["unknown_transport_requests_in_round"] == budget["unknown_provider_charges"]
            and execution["formal_case_conditions_retried"] is False
            and execution["operator_hash_correction_case_retried"] is False,
            "Execution limitations disagree with the round")
    require(execution["budget_rejected_conditions"] == [
        dict(case_id=row["case_id"], condition=row["condition"], provider_requests=row["provider_requests"],
             provider_stop_code=row["provider_stop_code"])
        for row in rows if row.get("provider_stop_code") == "upgrade_estimated_spend_limit"],
        "Batch budget rejections were concealed")
    require(execution["pressure_conditions_with_provider_request"] == sum(
        row["provider_requests"] > 0 for row in rows if row["condition"] in {"raw_history", "stale", "wrong"}),
        "Pressure assignments were confused with actual provider execution")
    demo = load(directory / "demo-trace.json")
    require(demo["case_id"] == "world-032" and demo["condition"] == "B2"
            and demo["actual_model_execution"] is True
            and demo["mid_investigation_post_original_revisions"] >= 1
            and indexed[(demo["case_id"], demo["condition"])]["assessment"]["strict_reference_passed"],
            "Selected demonstration disagrees with the result table")

    groups = analysis["groups"]
    folds = ("frozen", "challenge")
    code_close = all(
        groups[f + ":B0"]["mean_quality"] >= max(groups[f + ":" + c]["mean_quality"] for c in ("B0", "B1", "B2", "B3")) - .02
        and groups[f + ":B0"]["strict_reference_passed"] / groups[f + ":B0"]["runs"] >= max(
            groups[f + ":" + c]["strict_reference_passed"] / groups[f + ":" + c]["runs"] for c in ("B0", "B1", "B2", "B3")) - .05
        and groups[f + ":B0"]["unsafe_typed_draft_actions"] == 0 for f in folds)

    def qualifies(left, right):
        return all(analysis["paired_quality"][f + ":" + left + "-" + right]["mean_delta_quality"] >= .05
                   and analysis["paired_quality"][f + ":" + left + "-" + right]["family_cluster_interval"]["low"] > 0
                   and groups[f + ":" + left]["unsupported_claims"] <= groups[f + ":" + right]["unsupported_claims"]
                   and groups[f + ":" + left]["unsafe_typed_draft_actions"] <= groups[f + ":" + right]["unsafe_typed_draft_actions"]
                   for f in folds)

    expected_choice = ("B0_deterministic" if code_close else "B3_adaptive_reviewed_methods"
                       if qualifies("B3", "B1") and qualifies("B3", "B2")
                       else "B2_adaptive_no_memory" if qualifies("B2", "B1") else "B1_all_materials")
    require(analysis["default_strategy_recommendation"] == expected_choice, "Default strategy differs from the preregistered rule")

    recovery = load(directory / "recovery.json")
    require(recovery["generation"] == 2 and recovery["replayed_responses"] == 1
            and recovery["new_client_requests"] == 0 and recovery["external_model_requests"] == 0,
            "Recovery claim differs from the recorded offline probe")
    ui = load(directory / "ui-acceptance.json")
    require(ui["external_model_requests"] == 0 and ui["five_review_items_enable_approval"] is True
            and ui["unchecked_review_blocks_approval"] is True
            and ui["repeated_submission_returned_same_receipt"] is True,
            "Review acceptance declaration is incomplete")
    return dict(verified=True, artifacts=len(files), case_conditions=len(rows), budget=budget,
                default_strategy_recommendation=analysis["default_strategy_recommendation"],
                scope="portable_hashes_and_summary_consistency_only",
                not_verified=["provider_authenticity", "unuploaded_raw_facts", "all_free_prose_semantics",
                              "independent_expert_review", "real_business_benefit"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path,
                        default=Path(__file__).resolve().parents[1] / "docs/evidence/agent-value-v1")
    args = parser.parse_args()
    try:
        result = verify_pack(args.directory)
    except (ValueError, OSError, KeyError, TypeError, ZeroDivisionError) as exc:
        print(json.dumps(dict(verified=False, error=str(exc)), ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
