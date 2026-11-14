"""Pair B2S (no gate) with B2V4 (gate) per case: credits, deferrals, parallel paid reads, quality.

    python -m scripts.compare_gate_study <score.json>   (output of score_v4_study)
"""
import json
import sys
from collections import defaultdict

data = json.load(open(sys.argv[1], encoding="utf-8"))
by_case = defaultdict(dict)
for run in data["runs"]:
    by_case[run["case_id"]][run["label"]] = run

print(f"{'case':<28}{'label':<6}{'credits/stage':<22}{'deferred':<9}{'safe(raw)':<10}{'safe(guard)':<12}{'quality(g)':<11}run_cny")
totals = defaultdict(lambda: defaultdict(float))
for case, runs in sorted(by_case.items()):
    for label in ("B2S", "B2V4"):
        run = runs.get(label)
        if not run:
            continue
        st = [s for s in run["stages"] if s["delivered"]]
        credits = [s["credits_used"] for s in st]
        deferred = sum((s["planning"] or {}).get("premature_blocked", 0) for s in st)
        raw = sum(s["raw"]["business_safe_pass"] for s in st)
        gd = sum(s["guarded"]["business_safe_pass"] for s in st)
        q = sum(s["guarded"]["quality_score"] for s in st) / len(st)
        print(f"{case:<28}{label:<6}{str(credits):<22}{deferred:<9}{raw}/{len(st):<8}{gd}/{len(st):<10}{q:<11.1f}{run['run_cny']}")
        t = totals[label]
        t["stages"] += len(st); t["credits"] += sum(credits); t["deferred"] += deferred
        t["raw"] += raw; t["guard"] += gd; t["cny"] += float(run["run_cny"]); t["quality"] += q
for label, t in sorted(totals.items()):
    n = len(by_case)
    print(label, "mean credits/stage", round(t["credits"] / t["stages"], 3), "deferred", int(t["deferred"]),
          "safe raw", int(t["raw"]), "safe guard", int(t["guard"]), "of", int(t["stages"]),
          "mean quality(g)", round(t["quality"] / n, 1), "cny", round(t["cny"], 3))
