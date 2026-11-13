"""Validate the held-out cases offline and score the deterministic workflow (B0) on them. No model call."""
import tempfile
from pathlib import Path

from app.evaluation.v31_query_protocol import audit_case
from app.evaluation.v4_heldout import SEMANTIC, candidates
from app.evaluation.v4_score import score_stage_v4, summarize
from app.investigation.v3.trajectory import run_case
from tests.test_v3_source_access import start


def main():
    groups = {"semantic": [], "plain": []}
    for snapshot, reference in candidates():
        audit_case(snapshot, reference)
        name = snapshot["case_id"][len("v31-eval-"):]
        with tempfile.TemporaryDirectory() as tmp:
            _, _, source = start(Path(tmp), snapshot)
            result = run_case(source, "B0")
        rows = [score_stage_v4(snapshot, reference, p["stage"], p["evidence"], p["report"]) for p in result["phases"]]
        group = "semantic" if name in SEMANTIC else "plain"
        groups[group].extend(r["v4"] for r in rows)
        states = [p["report"]["source_statement"]["value"] for p in result["phases"]]
        want = [s["full_state"]["values"] for s in reference["stages"]]
        print(f"{name:<26}{group:<9}B0 states {states}  expected {want}  "
              f"strict {sum(r['legacy']['strict_author_reference_pass'] for r in rows)}/3 "
              f"safe {sum(r['v4']['business_safe_pass'] for r in rows)}/3")
    for group, rows in groups.items():
        s = summarize(rows)
        print(group, "stages", s["stages"], "strict", s["legacy_strict_pass"], "safe", s["business_safe_pass"],
              "gate failures", s["gate_failures"])


if __name__ == "__main__":
    main()
