"""Summarise the held-out study: B0 versus model conditions, semantic versus plain cases.

    python -m scripts.heldout_report <study_dir> <b0_json_out> ... (prints tables)
"""
from collections import defaultdict
import json
from pathlib import Path
import sys
import tempfile

from app.evaluation.v31_query_score import score_stage
from app.evaluation.v4_heldout import SEMANTIC, candidates
from app.evaluation.v4_score import score_stage_v4
from app.investigation.v3.trajectory import run_case
from tests.test_v3_source_access import start

study = Path(sys.argv[1])
cases = {s["case_id"]: (s, r) for s, r in candidates()}
group = lambda cid: "semantic" if cid[len("v31-eval-"):] in SEMANTIC else "plain"
rows = defaultdict(lambda: defaultdict(lambda: dict(stages=0, state_ok=0, safe=0, credits=0, runs=0)))

for folder in sorted(study.glob("*__*")):
    if "failed" in folder.name or not (folder / "trajectory.json").exists():
        continue
    t = json.loads((folder / "trajectory.json").read_bytes())
    snapshot, reference = cases[t["case_id"]]
    a = rows[t["label"]][group(t["case_id"])]
    a["runs"] += 1
    for p in t["phases"]:
        if p["report"] is None:
            continue
        legacy = score_stage(snapshot, reference, p["stage"], p["evidence"], p["report"])
        a["stages"] += 1
        a["state_ok"] += int(legacy["full_information_target_correct"])
    last = [p for p in t["phases"] if p["report"] is not None][-1]
    a["credits"] += snapshot["access_budget_units"] - last["source_state"]["catalog"]["remaining_cost_units"]

for index, (snapshot, reference) in enumerate(cases.values()):
    with tempfile.TemporaryDirectory() as tmp:
        _, _, source = start(Path(tmp), snapshot)
        result = run_case(source, "B0")
    a = rows["B0"][group(snapshot["case_id"])]
    a["runs"] += 1
    for p in result["phases"]:
        s = score_stage_v4(snapshot, reference, p["stage"], p["evidence"], p["report"])
        a["stages"] += 1
        a["state_ok"] += int(s["legacy"]["full_information_target_correct"])
        a["safe"] += int(s["v4"]["business_safe_pass"])
    last = result["phases"][-1]
    a["credits"] += snapshot["access_budget_units"] - last["source_state"]["catalog"]["remaining_cost_units"]

print("label    group     runs stages  customer-state correct   mean credits per run (end of stage 2)")
for label in sorted(rows):
    for g in ("semantic", "plain"):
        a = rows[label].get(g)
        if a and a["stages"]:
            print(f"{label:<8} {g:<9} {a['runs']:<4} {a['stages']:<6} {a['state_ok']}/{a['stages']:<22} {a['credits'] / a['runs']:.2f}")
