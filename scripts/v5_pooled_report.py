"""Pooled held-out comparison over 12 cases x 3 stages: B0, B1S, B4N, B2V4, B5.

    python -m scripts.v5_pooled_report <study_dir> [out.json]

Counts only runs that completed without an uncertain request. Prints missing cells explicitly.
"""
from collections import defaultdict
from decimal import Decimal
import json
from pathlib import Path
import sys
import tempfile

from app.evaluation.v31_query_score import score_stage
from app.evaluation.v4_heldout import SEMANTIC, candidates
from app.evaluation.v4_score import score_stage_v4
from app.investigation.v3.trajectory import run_case
from scripts.score_v4_study import score_run
from tests.test_v3_source_access import start

study = Path(sys.argv[1])
cases = {s["case_id"]: (s, r) for s, r in candidates()}
LABELS = ["B0", "B1S", "B4N", "B2V4", "B5"]
cell = defaultdict(lambda: dict(stages=0, raw=0, guarded=0, strict_guarded=0, state=0, credits=0.0, cny=Decimal(0), runs=0, probe_cny=Decimal(0),
                                routed_free=0, stage_total=0))
done = defaultdict(set)


def add(label, case_id, rows, cny, probe=0, free=0):
    a = cell[label]
    a["runs"] += 1
    done[label].add(case_id)
    a["cny"] += Decimal(str(cny))
    a["probe_cny"] += Decimal(str(probe))
    a["routed_free"] += free
    for r in rows:
        a["stage_total"] += 1
        if not r["delivered"]:
            continue
        a["stages"] += 1
        a["raw"] += r["raw"]["business_safe_pass"]
        a["guarded"] += r["guarded"]["business_safe_pass"]
        a["strict_guarded"] += r["guarded"]["legacy_strict_exact_pass"]
    a["credits"] += rows[-1]["credits_used"] if rows and rows[-1]["delivered"] else 0


for folder in sorted(study.glob("*__*")):
    if "failed" in folder.name or not (folder / "trajectory.json").exists():
        continue
    t = json.loads((folder / "trajectory.json").read_bytes())
    if t["blocked_uncertain_request"] or not t["complete"] or t["label"] not in LABELS:
        continue
    snap, ref = cases[t["case_id"]]
    rows = score_run(snap, ref, t)
    probes = [p["plan"].get("probe", {}) for p in t["phases"] if p.get("plan") and p["plan"].get("mode") == "b5"]
    free = sum(1 for p in t["phases"] if p.get("plan") and p["plan"].get("mode") == "b5" and p["plan"].get("route") == "B0")
    add(t["label"], t["case_id"], rows, t["financial"]["run_committed_estimate_cny"], sum(Decimal(x.get("cny", "0")) for x in probes), free)
    for p in t["phases"]:
        a = cell[t["label"]]
        legacy = score_stage(snap, ref, p["stage"], p["evidence"], p["report"]) if p["report"] else None
        a["state"] += int(bool(legacy and legacy["full_information_target_correct"]))

for cid, (snap, ref) in cases.items():
    with tempfile.TemporaryDirectory() as d:
        _, _, source = start(Path(d), snap)
        res = run_case(source, "B0")
    rows = []
    for p in res["phases"]:
        s = score_stage_v4(snap, ref, p["stage"], p["evidence"], p["report"])
        rows.append(dict(delivered=True, raw=s["v4"], guarded=s["v4"], credits_used=snap["access_budget_units"] - p["source_state"]["catalog"]["remaining_cost_units"]))
        cell["B0"]["state"] += int(s["legacy"]["full_information_target_correct"])
    add("B0", cid, rows, 0)
    cell["B0"]["guarded"] = cell["B0"]["raw"]

print(f"{'condition':<8}{'cases':>6}{'stages':>8}{'state ok':>9}{'Tier1 raw':>10}{'Tier1 guard':>12}{'strict guard':>13}{'credits/run':>12}{'model CNY/run':>14}{'free stages':>12}")
out = {}
for label in LABELS:
    a = cell[label]
    n = a["runs"] or 1
    out[label] = dict(cases=sorted(done[label]), stages=a["stages"], state_correct=a["state"], tier1_raw=a["raw"], tier1_guarded=a["guarded"],
                      strict_guarded=a["strict_guarded"], credits_per_run=round(a["credits"] / n, 2), model_cny_per_run=float(round((a["cny"] + a["probe_cny"]) / n, 4)),
                      free_stages=a["routed_free"], missing_cases=12 - a["runs"])
    print(f"{label:<8}{a['runs']:>6}{a['stages']:>8}{a['state']:>9}{a['raw']:>10}{a['guarded']:>12}{a['strict_guarded']:>13}{a['credits'] / n:>12.2f}{float((a['cny'] + a['probe_cny']) / n):>14.4f}{a['routed_free']:>12}")
if len(sys.argv) > 2:
    Path(sys.argv[2]).write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
