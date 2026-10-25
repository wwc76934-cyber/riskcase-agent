"""Independent SQL source/rule/score/list/outcome checks for neural capacity research."""

import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3

import numpy as np

from app.risk.capacity_v2 import load_plans
from app.risk.history import sha256_file
from app.risk.source_merchant_pack import write_json


def sql_selection(db, budget, policy):
    if policy == "reserve_20pct":
        query = """WITH candidates AS (
          SELECT *,ROW_NUMBER() OVER(PARTITION BY day ORDER BY neural_score DESC,tie DESC,transaction_id DESC) cr
          FROM population WHERE rule_match=1), tagged AS (
          SELECT p.*,CASE WHEN EXISTS(SELECT 1 FROM candidates c WHERE c.transaction_id=p.transaction_id AND c.cr<=?) THEN 1 ELSE 0 END reserved
          FROM population p), ranked AS (
          SELECT transaction_id,day,ROW_NUMBER() OVER(PARTITION BY day ORDER BY reserved DESC,neural_score DESC,tie DESC,transaction_id DESC) rn FROM tagged)
          SELECT transaction_id,day,rn FROM ranked WHERE rn<=?"""
        return set(db.execute(query, (budget//5, budget)))
    order = {"neural": "neural_score DESC,tie DESC,transaction_id DESC",
             "rule_only": "amount_cents DESC,tie DESC,transaction_id DESC",
             "rule_first": "rule_match DESC,neural_score DESC,tie DESC,transaction_id DESC"}[policy]
    condition = "WHERE rule_match=1" if policy == "rule_only" else ""
    return set(db.execute(f"WITH ranked AS (SELECT transaction_id,day,ROW_NUMBER() OVER(PARTITION BY day ORDER BY {order}) rn FROM population {condition}) SELECT transaction_id,day,rn FROM ranked WHERE rn<=?", (budget,)))


def sql_metrics(db, budget, policy, month=None):
    condition = "WHERE substr(p.day,1,7)=?" if month else ""
    parameters = [budget, policy, budget] + ([month] if month else [])
    values = db.execute(f"""SELECT COUNT(*),SUM(l.is_fraud),COUNT(DISTINCT p.day),
        SUM(s.transaction_id IS NOT NULL),SUM(s.transaction_id IS NOT NULL AND l.is_fraud=1),
        SUM(s.transaction_id IS NOT NULL AND b.transaction_id IS NULL),SUM(s.transaction_id IS NULL AND b.transaction_id IS NOT NULL),
        SUM(s.transaction_id IS NOT NULL AND b.transaction_id IS NULL AND l.is_fraud=1),
        SUM(s.transaction_id IS NULL AND b.transaction_id IS NOT NULL AND l.is_fraud=1)
        FROM population p JOIN outcomes.labels l ON p.transaction_id=l.transaction_id AND l.split='validation'
        LEFT JOIN plans.reviews s ON p.transaction_id=s.transaction_id AND s.budget=? AND s.policy=?
        LEFT JOIN plans.reviews b ON p.transaction_id=b.transaction_id AND b.budget=? AND b.policy='neural' {condition}""", parameters).fetchone()
    rows, positives, days, reviews, tp, added, displaced, gain, loss = map(int, values)
    return {"rows": rows, "positives": positives, "reviews": reviews, "true_positives": tp, "false_positives": reviews-tp,
            "false_negatives": positives-tp, "precision": tp/reviews if reviews else None, "recall": tp/positives if positives else None,
            "days": days, "capacity": days*budget, "unused_capacity": days*budget-reviews,
            "added_reviews": added, "displaced_reviews": displaced, "gained_tp": gain, "lost_tp": loss, "net_tp": gain-loss,
            "gained_fp": added-gain, "displaced_fp": displaced-loss, "review_delta": added-displaced}


def verify(root, directory):
    directory = Path(directory)
    protocol, manifest = load_plans(root, directory)
    report_path = directory / "report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if (report["status"] != "neural_capacity_development_evaluated" or report["baseline_kind"] != "neural_v2_ensemble"
            or report["human_approved"] or report["deployed"] or report["final_test_evaluated"]
            or report["research_review_status"] != "pending_review_unregistered_material"):
        raise ValueError("unapproved_neural_development_research_required")
    if report["protocol_sha256"] != sha256_file(directory / "protocol.json") or report["plan_manifest_sha256"] != sha256_file(directory / "plan-manifest.json"):
        raise ValueError("capacity_report_binding_changed")
    marker_path = directory / "labels-opened.json"
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    if marker.get("period") != "2018" or marker.get("plan_manifest_sha256") != sha256_file(directory / "plan-manifest.json") or marker.get("no_rule_or_allocation_tuning") is not True or sha256_file(marker_path) != report["labels_opened_sha256"]:
        raise ValueError("development_opening_binding_changed")
    labels = Path(protocol["labels_path"])
    if sha256_file(labels) != protocol["labels_sha256"]: raise ValueError("capacity_labels_changed")
    with closing(sqlite3.connect((directory / "ranking-inputs.sqlite").resolve().as_uri()+"?mode=ro", uri=True)) as db:
        db.execute("ATTACH DATABASE ? AS sourcefeatures", (Path(protocol["inputs"]["features"]["path"]).resolve().as_uri()+"?mode=ro",))
        db.execute("ATTACH DATABASE ? AS plans", ((directory / "planned-reviews.sqlite").resolve().as_uri()+"?mode=ro",))
        operations = {"gt": ">", "ge": ">=", "eq": "="}
        predicate = " AND ".join(f"f.{term['field']} {operations[term['op']]} ?" for term in protocol["rule"]["all"])
        values = [term["value"] for term in protocol["rule"]["all"]]
        source_count = db.execute("SELECT COUNT(*) FROM sourcefeatures.features WHERE split='validation' AND event_time>='2018-01-01' AND event_time<'2019-01-01'").fetchone()[0]
        if source_count != protocol["expected_rows"] or db.execute("SELECT COUNT(*) FROM population").fetchone()[0] != source_count:
            raise ValueError("neural_capacity_population_size_differs")
        joined, bad = db.execute(f"""SELECT COUNT(*),SUM(p.event_time IS NOT f.event_time OR p.day IS NOT substr(f.event_time,1,10)
            OR p.channel IS NOT f.channel OR p.amount_cents IS NOT f.amount_cents
            OR p.rule_match IS NOT CASE WHEN {predicate} THEN 1 ELSE 0 END)
            FROM population p JOIN sourcefeatures.features f USING(transaction_id)
            WHERE f.split='validation' AND f.event_time>='2018-01-01' AND f.event_time<'2019-01-01'""", values).fetchone()
        if joined != source_count or bad: raise ValueError("neural_capacity_source_or_rule_projection_differs")
        source_scores = np.load(protocol["inputs"]["scores"]["path"], allow_pickle=False)
        seed_scores = [np.load(protocol["inputs"][f"seed_{seed}"]["path"], allow_pickle=False) for seed in (42, 43, 44)]
        if source_scores.shape != (source_count,) or any(s.shape != source_scores.shape for s in seed_scores) or not np.array_equal(source_scores, np.mean(seed_scores, axis=0)):
            raise ValueError("source_three_seed_ensemble_differs")
        for index, (identity, score, tie) in enumerate(db.execute("SELECT transaction_id,neural_score,tie FROM population ORDER BY transaction_id")):
            expected_tie = hashlib.sha256((protocol["tie_seed"] + "\0" + identity).encode()).hexdigest()
            if score != float(source_scores[index]) or tie != expected_tie: raise ValueError("aligned_neural_source_score_or_tie_differs")
        keys = {(budget, policy) for budget in protocol["budgets"] for policy in protocol["policies"]}
        # A policy can legitimately yield no rows if no rule matches in a tiny fixture.
        observed = set(db.execute("SELECT DISTINCT budget,policy FROM plans.reviews"))
        if observed - keys: raise ValueError("unexpected_neural_capacity_policy_lists")
        for budget, policy in sorted(keys):
            expected = sql_selection(db, budget, policy)
            actual = set(db.execute("SELECT transaction_id,day,rank FROM plans.reviews WHERE budget=? AND policy=?", (budget, policy)))
            if actual != expected: raise ValueError("independent_neural_capacity_ranking_differs")
        db.execute("ATTACH DATABASE ? AS outcomes", (labels.resolve().as_uri()+"?mode=ro",))
        label_count, invalid, positives = db.execute("SELECT COUNT(*),SUM(CASE WHEN typeof(l.is_fraud)!='integer' OR l.is_fraud NOT IN (0,1) THEN 1 ELSE 0 END),SUM(l.is_fraud) FROM population p JOIN outcomes.labels l ON p.transaction_id=l.transaction_id AND l.split='validation'").fetchone()
        total_outcomes = db.execute("SELECT COUNT(*) FROM outcomes.labels WHERE split='validation'").fetchone()[0]
        if label_count != source_count or total_outcomes != source_count or invalid or positives != protocol["expected_positives"]:
            raise ValueError("complete_development_outcome_join_required")
        monthly_checks = 0
        for budget, policy in sorted(keys):
            if sql_metrics(db, budget, policy) != report["metrics"][f"{budget}/{policy}"]: raise ValueError("independent_neural_capacity_paired_metrics_differ")
            if budget == protocol["primary_budget"]:
                for month in sorted({r[0][:7] for r in db.execute("SELECT DISTINCT day FROM population")}):
                    if sql_metrics(db, budget, policy, month) != report["primary_monthly"][policy][month]:
                        raise ValueError("independent_neural_capacity_monthly_metrics_differ")
                    monthly_checks += 1
        actual_plan_rows = db.execute("SELECT COUNT(*) FROM plans.reviews").fetchone()[0]
    proof = {"status": "passed", "baseline_kind": "neural_v2_ensemble", "report_sha256": sha256_file(report_path),
             "protocol_sha256": sha256_file(directory / "protocol.json"), "plan_manifest_sha256": sha256_file(directory / "plan-manifest.json"),
             "script_sha256": sha256_file(Path(__file__)), "source_rows_checked": source_count, "ranking_lists_checked": len(keys),
             "planned_review_entries": actual_plan_rows, "monthly_checks": monthly_checks, "actual_review_database_unchanged": True,
             "actual_candidate_version": 2, "human_approved": False, "deployed": False, "final_test_evaluated": False}
    write_json(directory / "verification.json", proof)
    package = {"status": "pending_review_unregistered_material", "baseline_kind": "neural_v2_ensemble", "candidate_reference": protocol["candidate"],
               "rule": protocol["rule"], "protocol_sha256": proof["protocol_sha256"], "report_sha256": proof["report_sha256"],
               "verification_sha256": sha256_file(directory / "verification.json"), "primary_budget": 10,
               "primary_metrics": {policy: report["metrics"][f"10/{policy}"] for policy in protocol["policies"]},
               "actual_review_database_modified": False, "human_approved": False, "deployed": False,
               "scope": "New synthetic-development research evidence only; actual candidate v2 remains unchanged pending review."}
    write_json(directory / "research-package.json", package)
    return proof


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=root / "runs/capacity-neural-v2")
    args = parser.parse_args()
    print(json.dumps(verify(root, args.directory), ensure_ascii=False, indent=2))


if __name__ == "__main__": main()
