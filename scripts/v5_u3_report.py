"""U3 report: exhaustive fetch (B1S) vs unconstrained agent (B2S) vs precondition-gated agent (B2P).

    python -m scripts.v5_u3_report <study_dir> [out.json]

Uses the cost-ladder variants (primary 2, corroborating 2, fallback 6 credits). Only complete runs count.
"""
import json
from pathlib import Path
import sys

from app.evaluation.v5_cost_study import candidates
from app.investigation.v3.output import AcquiredEvidence
from app.investigation.v3.precondition_gate import feedback_metrics, roles_of
from app.investigation.v3.rule_workflow import customer_state, originals
from scripts.score_v4_study import score_run

study = Path(sys.argv[1])
cases = {s["case_id"]: (s, r) for s, r in candidates()}
NAMES = {"B1S": "S1 exhaustive fetch", "B2S": "S2 agent, no gate", "B2P": "S3 agent, precondition gate"}


def stage_records(snap, t):
    roles = roles_of(snap)
    primary = [i for i, r in roles.items() if r == "primary"]
    followup = {i for i, r in roles.items() if r in ("fallback", "corroborating")}
    out, seen_before = [], set()
    first_primary_turn = None
    for p in t["phases"]:
        results = p["source_state"]["catalog"].get("source_query_results", {})
        new = set(results) - seen_before
        trace = (p.get("plan") or {}).get("trace", [])
        # needs follow-up: primary unavailable, or unresolved by the rule reader
        needs = None
        pid = next((i for i in primary if i in results), None)
        if pid is not None:
            if results[pid]["status"] != "available":
                needs = True
            else:
                ledger = AcquiredEvidence(case_id=snap["case_id"], cutoff=snap["cutoff"], as_of=snap["stage_times"][p["stage"]],
                                          subject_id=snap["subject_id"], target_id=snap["alert"]["transaction_id"],
                                          transaction_ids=[x["transaction_id"] for x in snap["transactions"]]).ingest(p["evidence"])
                text = "\n".join(line["text"] for lines in originals(ledger) if lines[0][1]["source_id"] == pid for _, line in lines)
                others = [x["transaction_id"] for x in snap["transactions"] if x["transaction_id"] != ledger.target_id]
                needs = customer_state(text, ledger.target_id, others) == "unresolved"
        read_followup = bool(new & followup)
        primary_turn = next((s["model_turn"] for s in trace if s.get("ok") and set(s["source_ids"]) & set(primary)), None)
        follow_turn = next((s["model_turn"] for s in trace if s.get("ok") and set(s["source_ids"]) & followup), None)
        carried = pid is not None and pid in seen_before
        after = bool(read_followup and ((follow_turn is not None and primary_turn is not None and follow_turn > primary_turn) or carried))
        out.append(dict(primary_followup_needed=needs, paid_followup_read=read_followup, followup_after_primary_turn=after,
                        fallback_read_without_need=bool(read_followup and not needs),
                        deferred=sum(len(s.get("deferred_source_ids", [])) for s in trace)))
        seen_before |= set(results)
    return out


report = {}
for label, title in NAMES.items():
    stages, tier1, raw, credits, cny, runs, state_ok = [], 0, 0, 0, 0.0, 0, 0
    for folder in sorted(study.glob(f"*__{label}")):
        if "failed" in folder.name or not (folder / "trajectory.json").exists():
            continue
        t = json.loads((folder / "trajectory.json").read_bytes())
        if t["blocked_uncertain_request"] or not t["complete"]:
            continue
        snap, ref = cases[t["case_id"]]
        rows = score_run(snap, ref, t)
        runs += 1
        tier1 += sum(r["guarded"]["business_safe_pass"] for r in rows)
        raw += sum(r["raw"]["business_safe_pass"] for r in rows)
        credits += rows[-1]["credits_used"]
        cny += float(t["financial"]["run_committed_estimate_cny"])
        stages += stage_records(snap, t)
    m = feedback_metrics(stages)
    report[label] = dict(strategy=title, runs=runs, tier1_raw=raw, tier1_guarded=tier1,
                         credits_per_run=round(credits / runs, 2) if runs else None, model_cny_per_run=round(cny / runs, 4) if runs else None, **m)
base = report["B1S"]["credits_per_run"]
for label, r in report.items():
    r["credit_saving_vs_exhaustive"] = round(1 - r["credits_per_run"] / base, 3) if base and r["credits_per_run"] is not None else None
print(f"{'strategy':<30}{'runs':>5}{'Tier1 guard':>12}{'credits/run':>12}{'saving':>8}{'TFDR':>7}{'opp':>5}{'premature':>10}{'blocked':>8}{'CNY/run':>9}")
for label, r in report.items():
    tfdr = "-" if r["true_feedback_driven_rate"] is None else f"{r['true_feedback_driven_rate']:.2f}"
    prem = "-" if r["premature_fallback_rate"] is None else f"{r['premature_fallback_rate']:.2f}"
    print(f"{r['strategy']:<30}{r['runs']:>5}{str(r['tier1_guarded']) + '/' + str(r['stages']):>12}{r['credits_per_run']:>12}{r['credit_saving_vs_exhaustive']:>8}{tfdr:>7}{r['opportunities']:>5}{prem:>10}{r['blocked']:>8}{r['model_cny_per_run']:>9}")
if len(sys.argv) > 2:
    Path(sys.argv[2]).write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
