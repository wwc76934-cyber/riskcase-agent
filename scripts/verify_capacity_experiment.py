"""Independent source joins, SQL window rankings and paired outcome counts."""

import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3

import numpy as np

from app.risk.capacity_experiment import load_protocol
from app.risk.history import sha256_file
from app.risk.source_merchant_pack import write_json


def sql_selection(db,budget,policy):
    if policy=="reserve_20pct":
        sql="""WITH candidates AS (SELECT *,ROW_NUMBER() OVER(PARTITION BY day ORDER BY hgb_score DESC,tie DESC,transaction_id DESC) AS cr FROM population WHERE rule_match=1),
        tagged AS (SELECT p.*,CASE WHEN EXISTS(SELECT 1 FROM candidates c WHERE c.transaction_id=p.transaction_id AND c.cr<=?) THEN 1 ELSE 0 END AS reserved FROM population p),
        ranked AS (SELECT transaction_id,day,ROW_NUMBER() OVER(PARTITION BY day ORDER BY reserved DESC,hgb_score DESC,tie DESC,transaction_id DESC) AS rn FROM tagged)
        SELECT transaction_id,day,rn FROM ranked WHERE rn<=?"""
        return set(db.execute(sql,(budget//5,budget)))
    condition="WHERE rule_match=1" if policy=="rule_only" else ""
    order={"hgb":"hgb_score DESC,tie DESC,transaction_id DESC","rule_only":"amount_cents DESC,tie DESC,transaction_id DESC",
        "rule_first":"rule_match DESC,hgb_score DESC,tie DESC,transaction_id DESC"}[policy]
    return set(db.execute(f"WITH ranked AS (SELECT transaction_id,day,ROW_NUMBER() OVER(PARTITION BY day ORDER BY {order}) AS rn FROM population {condition}) SELECT transaction_id,day,rn FROM ranked WHERE rn<=?",(budget,)))


def sql_metrics(db,budget,policy,month=None):
    condition="WHERE substr(p.day,1,7)=?" if month else ""
    params=[budget,policy,budget]+([month] if month else [])
    row=db.execute(f"""SELECT COUNT(*),SUM(l.is_fraud),COUNT(DISTINCT p.day),
        SUM(s.transaction_id IS NOT NULL),SUM(s.transaction_id IS NOT NULL AND l.is_fraud=1),
        SUM(s.transaction_id IS NOT NULL AND b.transaction_id IS NULL),SUM(s.transaction_id IS NULL AND b.transaction_id IS NOT NULL),
        SUM(s.transaction_id IS NOT NULL AND b.transaction_id IS NULL AND l.is_fraud=1),
        SUM(s.transaction_id IS NULL AND b.transaction_id IS NOT NULL AND l.is_fraud=1)
        FROM population p JOIN outcomes.labels l ON p.transaction_id=l.transaction_id AND l.split='validation'
        LEFT JOIN plans.reviews s ON p.transaction_id=s.transaction_id AND s.budget=? AND s.policy=?
        LEFT JOIN plans.reviews b ON p.transaction_id=b.transaction_id AND b.budget=? AND b.policy='hgb' {condition}""",params).fetchone()
    rows,positives,days,reviews,tp,added,displaced,gain,loss=map(int,row)
    return {"rows":rows,"positives":positives,"reviews":reviews,"true_positives":tp,"false_positives":reviews-tp,"false_negatives":positives-tp,
        "precision":tp/reviews if reviews else None,"recall":tp/positives if positives else None,"days":days,"capacity":days*budget,"unused_capacity":days*budget-reviews,
        "added_reviews":added,"displaced_reviews":displaced,"gained_tp":gain,"lost_tp":loss,"net_tp":gain-loss,"gained_fp":added-gain,"displaced_fp":displaced-loss,"review_delta":added-displaced}


def verify(root,directory):
    protocol=load_protocol(root,directory)
    report_path=directory / "report.json"
    report=json.loads(report_path.read_text(encoding="utf-8"));manifest=json.loads((directory / "plan-manifest.json").read_text())
    if report["status"]!="capacity_development_evaluated" or report["human_approved"] or report["deployed"] or report["final_test_evaluated"]: raise ValueError("Unapproved development replay required")
    if report["protocol_sha256"]!=sha256_file(directory / "protocol.json") or report["plan_manifest_sha256"]!=sha256_file(directory / "plan-manifest.json"):
        raise ValueError("Report bindings changed")
    if manifest["protocol_sha256"]!=report["protocol_sha256"] or manifest["labels_read"] is not False or not manifest["baseline_top10_matches_original"]: raise ValueError("Pre-label plan proof missing")
    population=directory / "ranking-inputs.sqlite";plans=directory / "planned-reviews.sqlite"
    if sha256_file(population)!=manifest["population_sha256"] or sha256_file(plans)!=manifest["plans_sha256"]: raise ValueError("Frozen lists changed")
    labels=Path(protocol["labels_path"])
    if sha256_file(labels)!=protocol["labels_sha256"]: raise ValueError("Labels changed")
    with closing(sqlite3.connect(population.resolve().as_uri()+"?mode=ro",uri=True)) as db:
        db.execute("ATTACH DATABASE ? AS features_source",(Path(protocol["inputs"]["features"]["path"]).as_uri()+"?mode=ro",))
        db.execute("ATTACH DATABASE ? AS plans",(plans.resolve().as_uri()+"?mode=ro",))
        ops={"gt":">","ge":">=","eq":"="}
        predicate=" AND ".join(f"f.{t['field']} {ops[t['op']]} ?" for t in protocol["rule"]["all"])
        values=[t["value"] for t in protocol["rule"]["all"]]
        source_count=db.execute("SELECT COUNT(*) FROM features_source.features WHERE split='validation' AND event_time>='2018-01-01' AND event_time<'2019-01-01'").fetchone()[0]
        if source_count!=protocol["expected_rows"] or db.execute("SELECT COUNT(*) FROM population").fetchone()[0]!=source_count: raise ValueError("Population size differs")
        count,bad=db.execute(f"""SELECT COUNT(*),SUM(p.event_time!=f.event_time OR p.day!=substr(f.event_time,1,10) OR p.amount_cents!=f.amount_cents OR p.rule_match!=CASE WHEN {predicate} THEN 1 ELSE 0 END)
            FROM population p JOIN features_source.features f ON p.transaction_id=f.transaction_id WHERE f.split='validation' AND f.event_time>='2018-01-01' AND f.event_time<'2019-01-01'""",values).fetchone()
        if count!=source_count or bad: raise ValueError("Source features or independent rule match differs")
        with np.load(protocol["inputs"]["scores"]["path"],allow_pickle=False) as archive:
            scores={str(i):float(s) for i,s in zip(archive["transaction_id"],archive["hist_gradient_boosting"])}
        for identity,score,tie in db.execute("SELECT transaction_id,hgb_score,tie FROM population"):
            expected=hashlib.sha256((protocol["tie_seed"]+"\0"+identity).encode()).hexdigest()
            if scores.pop(identity,None)!=score or tie!=expected: raise ValueError("Source score/tie differs")
        if scores: raise ValueError("Source scores outside population")
        keys={(k,p) for k in protocol["budgets"] for p in protocol["policies"]}
        if set(db.execute("SELECT DISTINCT budget,policy FROM plans.reviews"))!=keys: raise ValueError("Missing or extra policy lists")
        for budget,policy in sorted(keys):
            expected=sql_selection(db,budget,policy)
            actual=set(db.execute("SELECT transaction_id,day,rank FROM plans.reviews WHERE budget=? AND policy=?",(budget,policy)))
            if actual!=expected: raise ValueError("Independent SQL ranking differs: "+str((budget,policy)))
        original={r["transaction_id"] for r in (json.loads(line) for line in Path(protocol["inputs"]["baseline_queue"]["path"]).open(encoding="utf-8")) if r["strategy"]=="hist_gradient_boosting"}
        if original!={r[0] for r in sql_selection(db,10,"hgb")}: raise ValueError("Original top-10 baseline differs")
        db.execute("ATTACH DATABASE ? AS outcomes",(labels.as_uri()+"?mode=ro",))
        monthly_checks=0
        for budget,policy in sorted(keys):
            if sql_metrics(db,budget,policy)!=report["metrics"][f"{budget}/{policy}"]: raise ValueError("Paired metrics differ")
            if budget==protocol["primary_budget"]:
                for month in sorted({r[0][:7] for r in db.execute("SELECT DISTINCT day FROM population")}):
                    if sql_metrics(db,budget,policy,month)!=report["primary_monthly"][policy][month]: raise ValueError("Monthly metrics differ")
                    monthly_checks+=1
    proof={"status":"passed","report_sha256":sha256_file(report_path),"protocol_sha256":sha256_file(directory / "protocol.json"),
        "plan_manifest_sha256":sha256_file(directory / "plan-manifest.json"),"script_sha256":sha256_file(Path(__file__)),
        "source_rows_checked":source_count,"ranking_lists_checked":len(keys),"monthly_checks":monthly_checks,"final_test_evaluated":False,
        "limits":"Independent SQL recomputation on previously used synthetic development data, not external business validation."}
    write_json(directory / "verification.json",proof)
    return proof


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--directory",type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(verify(Path(__file__).resolve().parents[1],args.directory.resolve()),indent=2))
