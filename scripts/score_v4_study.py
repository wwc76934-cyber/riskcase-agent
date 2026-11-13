"""Score saved V4 study trajectories offline: legacy, V4 raw, V4 with Policy Guard, and credits.

    python -m scripts.score_v4_study <dataset> <study_dir> [out.json]
"""
import argparse
from collections import defaultdict
import json
from pathlib import Path

from app.evaluation.v31_query_protocol import read_dataset
from app.evaluation.v31_query_score import score_stage
from app.evaluation.v4_score import summarize, tiered
from app.investigation.v3.output import AcquiredEvidence
from app.investigation.v3.policy_guard import apply_policy_guard
from app.investigation.v3.tier_gate import planning_metrics


def credits_used(phase, snapshot):
    return snapshot["access_budget_units"] - phase["source_state"]["catalog"]["remaining_cost_units"]


def score_run(snapshot, reference, trajectory):
    total_cost = sum(r["cost_units"] for r in snapshot["records"])
    rows = []
    for phase in trajectory["phases"]:
        stage, report = phase["stage"], phase["report"]
        truth = reference["stages"][stage]
        if report is None:
            rows.append(dict(stage=stage, delivered=False, error=phase["error"],
                             blocked=phase["blocked_uncertain_request"]))
            continue
        used = credits_used(phase, snapshot)
        legacy = score_stage(snapshot, reference, stage, phase["evidence"], report)
        raw = tiered(legacy, truth, report, credits_used=used, credits_full=total_cost)
        ledger = AcquiredEvidence(case_id=snapshot["case_id"], cutoff=snapshot["cutoff"],
            as_of=snapshot["stage_times"][stage], subject_id=snapshot["subject_id"],
            transaction_ids=[r["transaction_id"] for r in snapshot["transactions"]],
            target_id=snapshot["alert"]["transaction_id"]).ingest(phase["evidence"])
        guarded_report, audit = apply_policy_guard(report, ledger)
        guarded = tiered(score_stage(snapshot, reference, stage, phase["evidence"], guarded_report),
                         truth, guarded_report, credits_used=used, credits_full=total_cost)
        trace = (phase.get("plan") or {}).get("trace", [])
        rows.append(dict(stage=stage, delivered=True, raw=raw, guarded=guarded, guard_audit=audit,
                         credits_used=used, credits_full_sweep=total_cost,
                         planning=planning_metrics(trace, phase["source_state"]["catalog"]["sources"]) if trace else None,
                         elapsed_seconds=phase["process_elapsed_seconds_including_recovery"]))
    return rows


def main(dataset, study, out=None):
    cases = {e["case_id"]: (s, r) for _, e, s, r in read_dataset(dataset)}
    by_label, runs = defaultdict(lambda: dict(raw=[], guarded=[], undelivered=0, credits=[])), []
    for folder in sorted(Path(study).glob("*__*")):
        path = folder / "trajectory.json"
        if not path.exists() or "failed" in folder.name:
            continue
        trajectory = json.loads(path.read_bytes())
        snapshot, reference = cases[trajectory["case_id"]]
        rows = score_run(snapshot, reference, trajectory)
        label = trajectory["label"]
        bucket = by_label[label]
        for row in rows:
            if row["delivered"]:
                bucket["raw"].append(row["raw"])
                bucket["guarded"].append(row["guarded"])
                bucket["credits"].append(row["credits_used"])
            else:
                bucket["undelivered"] += 1
        runs.append(dict(case_id=trajectory["case_id"], label=label, blocked=trajectory["blocked_uncertain_request"],
                         run_cny=trajectory["financial"]["run_committed_estimate_cny"], stages=rows))
    summary = {label: dict(delivered_stages=len(b["raw"]), undelivered_stages=b["undelivered"],
                           raw=summarize(b["raw"]) if b["raw"] else None,
                           with_policy_guard=summarize(b["guarded"]) if b["guarded"] else None,
                           mean_credits_per_stage=round(sum(b["credits"]) / len(b["credits"]), 3) if b["credits"] else None)
               for label, b in sorted(by_label.items())}
    report = dict(scope="v4_study_offline_scoring", summary=summary, runs=runs)
    if out:
        Path(out).write_text(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("study", type=Path)
    parser.add_argument("out", type=Path, nargs="?")
    a = parser.parse_args()
    print(json.dumps(main(a.dataset, a.study, a.out)["summary"], indent=2, sort_keys=True))
