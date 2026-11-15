"""Offline re-score of already-delivered Gate 4.1 reports with the V4 two-tier evaluator.

No model request is made. Legacy strict results are recomputed alongside so the
two views can be compared on the same 17 delivered stages.

    python -m scripts.re_evaluate_g4_offline <dataset> <preregistration> <live> [out.json]
"""
import argparse
from collections import defaultdict
import json
from pathlib import Path

from app.evaluation.gate4_formal_tranche import _read_result, phase_rows
from app.evaluation.v31_query_runner import registered_plan
from app.evaluation.v31_query_score import score_stage
from app.evaluation.v4_score import summarize, tiered
from app.investigation.v3.output import AcquiredEvidence
from app.investigation.v3.policy_guard import apply_policy_guard


def rescore(dataset, preregistration, live):
    protocol, schedule, cases = registered_plan(dataset, preregistration)
    slots = [slot for slot in schedule if slot["condition"] != "B0"]
    by_condition, guarded_by_condition, detail = defaultdict(list), defaultdict(list), []
    for index, folder in enumerate(sorted(Path(live).glob("slot-*"))):
        result = _read_result(folder)
        expected = slots[index]
        if any(result[key] != value for key, value in expected.items()):
            raise ValueError("Formal result does not match its registered slot")
        _, snapshot, reference = cases[result["case_id"]]
        trajectory = json.loads((folder / "live" / "trajectory.json").read_bytes())
        rows = json.loads(json.dumps(phase_rows(snapshot, reference, trajectory)))
        for row, raw in zip(rows, trajectory["phases"], strict=True):
            assessment = row["assessment"]
            if not assessment["delivered"]:
                continue
            truth = reference["stages"][row["stage"]]
            score = tiered(assessment, truth, raw["report"])
            ledger = AcquiredEvidence(case_id=snapshot["case_id"], cutoff=snapshot["cutoff"],
                as_of=snapshot["stage_times"][row["stage"]], subject_id=snapshot["subject_id"],
                transaction_ids=[r["transaction_id"] for r in snapshot["transactions"]],
                target_id=snapshot["alert"]["transaction_id"]).ingest(raw["evidence"])
            guarded, audit = apply_policy_guard(raw["report"], ledger)
            guarded_score = tiered(score_stage(snapshot, reference, row["stage"], raw["evidence"], guarded),
                                   truth, guarded)
            score["guarded"] = guarded_score
            guarded_by_condition[result["condition"]].append(guarded_score)
            by_condition[result["condition"]].append(score)
            detail.append(dict(slot=folder.name, case_id=result["case_id"], condition=result["condition"],
                               stage=row["stage"], business_safe_pass=score["business_safe_pass"],
                               legacy_strict_exact_pass=score["legacy_strict_exact_pass"],
                               failed_gates=[g for g, ok in score["gates"].items() if not ok],
                               benign_extra_actions=score["benign_extra_actions"],
                               quality_score=score["quality_score"],
                               guarded_business_safe_pass=guarded_score["business_safe_pass"],
                               guarded_failed_gates=[g for g, ok in guarded_score["gates"].items() if not ok],
                               guard_audit=audit))
    everything = [score for rows in by_condition.values() for score in rows]
    all_guarded = [score for rows in guarded_by_condition.values() for score in rows]
    return dict(scope="offline_rescore_of_delivered_stages_no_model_requests",
                cost_scored=False, note="quality_score excludes cost efficiency (no per-tool credits in archived runs)",
                overall=summarize(everything), by_condition={c: summarize(r) for c, r in sorted(by_condition.items())},
                with_policy_guard=dict(overall=summarize(all_guarded),
                                       by_condition={c: summarize(r) for c, r in sorted(guarded_by_condition.items())}),
                stages=detail)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("preregistration", type=Path)
    parser.add_argument("live", type=Path)
    parser.add_argument("output", type=Path, nargs="?")
    args = parser.parse_args()
    report = rescore(args.dataset, args.preregistration, args.live)
    text = json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False)
    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")
    print(json.dumps(dict(overall=report["overall"], with_policy_guard=report["with_policy_guard"]),
                     indent=2, sort_keys=True))
