"""Freeze one candidate, then evaluate added workload against frozen HGB reviews."""

from contextlib import closing
import json
from pathlib import Path
import sqlite3
import subprocess
from uuid import uuid4

from app.risk.candidate_rules import FIELDS, compile_candidate, replay_candidate
from app.risk.history import sha256_file


def replay_rule(spec, *, root, selection, proposal_sha256=None):
    compile_candidate(spec)
    manifest_path = root / "data/features/features-b9692149f1c1/manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    features = manifest_path.parent / "features.sqlite"
    if manifest["labels_read"] is not False or sha256_file(features)!=manifest["features_sha256"]: raise ValueError("Changed features")
    cohort_path = Path(manifest["cohort_report"])
    if sha256_file(cohort_path)!=manifest["cohort_report_sha256"]: raise ValueError("Changed cohort")
    cohort = json.loads(cohort_path.read_text(encoding="utf-8"))
    baseline_dir = root / "runs/ml-v1-90f95014b365"
    baseline = json.loads((baseline_dir / "report.json").read_text(encoding="utf-8"))
    queue = baseline_dir / "daily-review.jsonl"
    if sha256_file(queue)!=baseline["artifact_sha256"]["daily-review.jsonl"]: raise ValueError("Changed review baseline")
    ids = [json.loads(line)["transaction_id"] for line in queue.read_text(encoding="utf-8").splitlines()
        if json.loads(line)["strategy"]=="hist_gradient_boosting"]
    if len(ids)!=3650 or len(set(ids))!=len(ids): raise ValueError("Unexpected baseline review IDs")
    old_thresholds = json.loads((root / "runs/rule-baseline-ee0d2eaf75f9/frozen-rules.json").read_text(encoding="utf-8"))
    output = root / "runs" / f"candidate-replay-v1-{uuid4().hex[:12]}"
    output.mkdir()
    frozen = {"rule":spec,"features_sha256":manifest["features_sha256"],"baseline_review_sha256":sha256_file(queue),
        "features_manifest_sha256":sha256_file(manifest_path),"cohort_report_sha256":sha256_file(cohort_path),
        "baseline_report_sha256":sha256_file(baseline_dir / "report.json"),
        "training_rule_artifact_sha256":sha256_file(root / "runs/rule-baseline-ee0d2eaf75f9/frozen-rules.json"),
        "code_commit":subprocess.check_output(["git","rev-parse","HEAD"],cwd=root,text=True).strip(),
        "selection":selection,"proposal_sha256":proposal_sha256,
        "labels_opened_after_freeze_only":True,"policy":"Additive to existing 10/day HGB reviews, NO fixed total daily budget and NO deployment."}
    (output / "frozen-candidate.json").write_text(json.dumps(frozen,indent=2)+"\n",encoding="utf-8")
    labels = cohort_path.parent / "dataset/evaluation-labels.sqlite"
    if sha256_file(labels)!=cohort["artifact_sha256"]["dataset/evaluation-labels.sqlite"]: raise ValueError("Changed labels")
    with closing(sqlite3.connect(features.resolve().as_uri()+"?mode=ro",uri=True)) as db:
        db.execute("ATTACH DATABASE ? AS evaluation",(labels.resolve().as_uri()+"?mode=ro",))
        db.row_factory = sqlite3.Row
        columns = ",".join("f."+field for field in sorted(FIELDS))
        cursor = db.execute(f"SELECT f.transaction_id,f.split,{columns},l.is_fraud FROM main.features f "
            "JOIN evaluation.labels l ON f.transaction_id=l.transaction_id AND f.split=l.split WHERE f.split='validation'")
        def records():
            for record in cursor:
                row = dict(record)
                if row["is_fraud"] not in (0,1): raise ValueError("Invalid label")
                row["is_fraud"] = bool(row["is_fraud"])
                yield row
        metrics = replay_candidate(records(),ids,spec)
    expected = cohort["partitions"]["validation"]
    if metrics["rows"]!=expected["rows"] or metrics["positives"]!=expected["fraud_rows"]: raise ValueError("Incomplete label join")
    if metrics["baseline"]["true_positives"]!=95: raise ValueError("Frozen baseline disagrees with prior evaluation")
    report = {"status":"development_replay_completed","data_kind":"synthetic","metrics":metrics,
        "frozen_candidate_sha256":sha256_file(output / "frozen-candidate.json"),"final_test_evaluated":False,
        "real_LLM_requests":0,"human_approved":False,"deployed":False,
        "limits":["Added reviews increase workload; not a same-budget model comparison.",
            "Unknown label availability: retrospective event-time experiment only.",
            "Candidate is an unapproved development hypothesis; no independent held-out or business benefit claim."]}
    (output / "report.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    return {"output_directory":str(output),**report}


def main():
    root = Path(__file__).resolve().parents[2]
    spec = json.loads((root / "config/candidate-rule-v1.json").read_text(encoding="utf-8"))
    thresholds = json.loads((root / "runs/rule-baseline-ee0d2eaf75f9/frozen-rules.json").read_text(encoding="utf-8"))["thresholds"]
    if spec["all"][0]!={"field":"amount_cents","op":"gt","value":thresholds["large_positive_amount_cents"]}:
        raise ValueError("Manual first hypothesis reuses frozen training amount threshold")
    print(json.dumps(replay_rule(spec,root=root,selection="Manually proposed after development investigation; training threshold reused. Not independent evaluation."),indent=2))


if __name__ == "__main__": main()
