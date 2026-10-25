"""Recompute candidate/overlap/increment counts with SQL, not Python replay."""

import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3

from app.risk.candidate_rules import compile_candidate
from app.risk.history import sha256_file


def verify(report_path):
    root = Path(__file__).resolve().parents[1]
    report = json.loads(report_path.read_text(encoding="utf-8"))
    frozen_path = report_path.parent / "frozen-candidate.json"
    if sha256_file(frozen_path)!=report["frozen_candidate_sha256"]: raise ValueError("Frozen candidate changed")
    frozen = json.loads(frozen_path.read_text(encoding="utf-8"))
    compile_candidate(frozen["rule"])
    manifest_path = root / "data/features/features-b9692149f1c1/manifest.json"
    cohort_path = root / "data/cohorts/cohort-3bfdbaab486f/build-report.json"
    features,labels = manifest_path.parent / "features.sqlite",cohort_path.parent / "dataset/evaluation-labels.sqlite"
    queue = root / "runs/ml-v1-90f95014b365/daily-review.jsonl"
    for path,key in ((features,"features_sha256"),(manifest_path,"features_manifest_sha256"),
        (cohort_path,"cohort_report_sha256"),(queue,"baseline_review_sha256")):
        if sha256_file(path)!=frozen[key]: raise ValueError("Replay input changed")
    cohort = json.loads(cohort_path.read_text(encoding="utf-8"))
    if sha256_file(labels)!=cohort["artifact_sha256"]["dataset/evaluation-labels.sqlite"]: raise ValueError("Labels changed")
    ids = [json.loads(line)["transaction_id"] for line in queue.read_text(encoding="utf-8").splitlines()
        if json.loads(line)["strategy"]=="hist_gradient_boosting"]
    ops = {"gt":">","ge":">=","eq":"="}
    predicate = " AND ".join(f'f.{term["field"]} {ops[term["op"]]} ?' for term in frozen["rule"]["all"])
    parameters = [term["value"] for term in frozen["rule"]["all"]]
    selections = ["COUNT(*) AS rows","SUM(outcome) AS positives","SUM(is_base AND is_candidate) AS overlap_alerts"]
    groups = {"baseline":"is_base","candidate":"is_candidate","union":"(is_base OR is_candidate)","added":"(is_candidate AND NOT is_base)"}
    for name,condition in groups.items():
        selections.extend([f"SUM({condition}) AS {name}_alerts",f"SUM(({condition}) AND outcome=1) AS {name}_tp"])
    with closing(sqlite3.connect(features.resolve().as_uri()+"?mode=ro",uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute("CREATE TEMP TABLE baseline(transaction_id TEXT PRIMARY KEY)")
        db.executemany("INSERT INTO baseline VALUES (?)",[(identity,) for identity in ids])
        db.execute("ATTACH DATABASE ? AS evaluation",(labels.resolve().as_uri()+"?mode=ro",))
        sql = f"""WITH replay AS (SELECT l.is_fraud AS outcome,
            (b.transaction_id IS NOT NULL) AS is_base, CASE WHEN {predicate} THEN 1 ELSE 0 END AS is_candidate
            FROM main.features f JOIN evaluation.labels l ON f.transaction_id=l.transaction_id AND f.split=l.split
            LEFT JOIN temp.baseline b ON f.transaction_id=b.transaction_id WHERE f.split='validation')
            SELECT {','.join(selections)} FROM replay"""
        counts = dict(db.execute(sql,parameters).fetchone())
    metrics = report["metrics"]
    for key in ("rows","positives","overlap_alerts"):
        if metrics[key]!=counts[key]: raise ValueError("SQL population/overlap differs")
    for group in groups:
        alerts,tp = counts[group+"_alerts"],counts[group+"_tp"]
        expected = {"alerts":alerts,"true_positives":tp,"false_positives":alerts-tp,
            "precision":tp/alerts if alerts else None,"recall":tp/counts["positives"] if counts["positives"] else None}
        if metrics[group]!=expected: raise ValueError("SQL incremental counts differ")
    if report["final_test_evaluated"] is not False: raise ValueError("Final test access forbidden")
    result = {"status":"passed","report_sha256":sha256_file(report_path),"verification_script_sha256":sha256_file(Path(__file__)),"sql_rows_verified":counts["rows"],
        "added_true_positives":counts["added_tp"],"added_false_positives":counts["added_alerts"]-counts["added_tp"],
        "final_test_evaluated":False,"limits":"Retrospective synthetic development replay; no deployment or provider calls."}
    (report_path.parent / "sql-verification.json").write_text(json.dumps(result,indent=2)+"\n",encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report",required=True,type=Path)
    print(json.dumps(verify(parser.parse_args().report),indent=2))
