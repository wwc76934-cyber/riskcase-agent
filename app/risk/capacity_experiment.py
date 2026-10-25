"""Freeze protocol, materialize label-free plans, evaluate development outcomes."""

from collections import defaultdict
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import numpy as np

from app.risk.candidate_rules import FIELDS,compile_candidate
from app.risk.capacity_policy import POLICIES,TIE_SEED,allocate_day,outcome_metrics,tie_key
from app.risk.history import sha256_file
from app.risk.source_merchant_pack import write_json


CODE_FILES=("app/risk/capacity_policy.py","app/risk/capacity_experiment.py","app/cli/capacity_experiment.py","scripts/verify_capacity_experiment.py",
    "app/risk/candidate_rules.py","app/risk/review_package.py","app/risk/review_store.py","app/risk/history.py","app/risk/source_merchant_pack.py")


def freeze(root,directory):
    if directory.exists(): raise ValueError("New experiment directory required")
    proposal=root / "runs/proposal-live-v1-4de79c571105/summary.json"
    from app.risk.review_package import review_package
    provenance=review_package(proposal)
    spec=provenance["rule"];compile_candidate(spec)
    cohort=root / "data/cohorts/cohort-3bfdbaab486f/build-report.json"
    features=root / "data/features/features-b9692149f1c1/manifest.json"
    detector=root / "runs/ml-v1-90f95014b365/report.json"
    cohort_data=json.loads(cohort.read_text());feature_data=json.loads(features.read_text());detector_data=json.loads(detector.read_text())
    scores=detector.parent / "validation-scores.npz";queue=detector.parent / "daily-review.jsonl"
    feature_db=features.parent / "features.sqlite";labels=cohort.parent / "dataset/evaluation-labels.sqlite"
    if feature_data["labels_read"] is not False or detector_data["final_test_read"]: raise ValueError("Development feature-only inputs required")
    if sha256_file(feature_db)!=feature_data["features_sha256"] or sha256_file(scores)!=detector_data["artifact_sha256"][scores.name] or sha256_file(queue)!=detector_data["artifact_sha256"][queue.name]:
        raise ValueError("Frozen source artifacts changed")
    if sha256_file(cohort)!=feature_data["cohort_report_sha256"]: raise ValueError("Cohort link changed")
    directory.mkdir(parents=True,exist_ok=False)
    protocol={"version":1,"status":"protocol_frozen","data_kind":"IBM_TabFormer_synthetic","split":"validation","period":["2018-01-01","2019-01-01"],
        "runtime":{"python":sys.version.split()[0],"numpy":np.__version__,"sqlite":sqlite3.sqlite_version},
        "budgets":[5,10,20],"primary_budget":10,"policies":list(POLICIES),"tie_seed":TIE_SEED,"rule":spec,"proposal_path":str(proposal.resolve()),
        "original_review_candidate_id":"case-467716-rule","proposal_package":provenance,
        "inputs":{name:{"path":str(path.resolve()),"sha256":sha256_file(path)} for name,path in (("cohort",cohort),("features_manifest",features),("features",feature_db),("detector",detector),("scores",scores),("baseline_queue",queue))},
        "labels_path":str(labels.resolve()),"labels_sha256":cohort_data["artifact_sha256"]["dataset/evaluation-labels.sqlite"],
        "expected_rows":cohort_data["partitions"]["validation"]["rows"],"expected_positives":cohort_data["partitions"]["validation"]["fraud_rows"],
        "expected_baseline_top10_tp":95,
        "code_file_sha256":{name:sha256_file(root / name) for name in CODE_FILES},
        "code_commit":subprocess.check_output(["git","rev-parse","HEAD"],cwd=root,text=True).strip(),
        "ranking":{"hgb":"HGB score descending","rule_only":"Only matched rows; positive amount descending; unused seats remain empty",
            "reserve_20pct":"First floor(K/5) rule matches by HGB score; fill remaining seats by HGB excluding reserved rows",
            "rule_first":"All rule matches before nonmatches, HGB score descending within each group"},
        "gate":"Research evidence only; no automatic approval, deployment or policy selection based on this development replay.",
        "limits":["2018 has been used in previous development analyses; this is NOT an untouched holdout.",
            "H1/H2 and monthly summaries are descriptive, not independent confirmation.","End-of-day batch review, not real-time transaction intervention.",
            "No loss/cost/label-confirmation timestamps: no business profit or online effectiveness claim.","2019 final detector test remains sealed."]}
    write_json(directory / "protocol.json",protocol)
    return protocol


def load_protocol(root,directory):
    protocol=json.loads((directory / "protocol.json").read_text(encoding="utf-8"))
    for name,digest in protocol["code_file_sha256"].items():
        if sha256_file(root / name)!=digest: raise ValueError("Code changed after protocol freeze: "+name)
    for item in protocol["inputs"].values():
        if sha256_file(Path(item["path"]))!=item["sha256"]: raise ValueError("Frozen input changed")
    if protocol["split"]!="validation" or protocol["period"]!=["2018-01-01","2019-01-01"]: raise ValueError("Final test access forbidden")
    compile_candidate(protocol["rule"])
    return protocol


def materialize(root,directory):
    protocol=load_protocol(root,directory)
    population_path=directory / "ranking-inputs.sqlite";plan_path=directory / "planned-reviews.sqlite"
    if population_path.exists() or plan_path.exists(): raise ValueError("Plans already exist; do not overwrite a frozen selection")
    rule=compile_candidate(protocol["rule"])
    with np.load(protocol["inputs"]["scores"]["path"],allow_pickle=False) as archive:
        identities=archive["transaction_id"];scores=archive["hist_gradient_boosting"];stamps=archive["event_time"]
    if len(identities)!=protocol["expected_rows"] or len(set(identities))!=len(identities) or len(scores)!=len(identities) or len(stamps)!=len(identities): raise ValueError("Score universe mismatch")
    score_map={str(identity):(float(score),str(stamp)) for identity,score,stamp in zip(identities,scores,stamps)}
    days=defaultdict(list)
    with closing(sqlite3.connect(Path(protocol["inputs"]["features"]["path"]).as_uri()+"?mode=ro",uri=True)) as db:
        db.row_factory=sqlite3.Row
        cursor=db.execute("SELECT transaction_id,event_time,"+",".join(sorted(FIELDS))+" FROM features WHERE split='validation' AND event_time>='2018-01-01' AND event_time<'2019-01-01'")
        for record in cursor:
            record=dict(record);identity=record["transaction_id"]
            score,stamp=score_map.pop(identity)
            if stamp!=record["event_time"]: raise ValueError("Score time differs from source")
            row={"transaction_id":identity,"event_time":stamp,"day":stamp[:10],"amount_cents":record["amount_cents"],"hgb_score":score,"rule_match":rule(record),"tie":tie_key(identity)}
            days[row["day"]].append(row)
    if score_map: raise ValueError("Scores outside feature universe")
    with closing(sqlite3.connect(population_path)) as db,db:
        db.execute("CREATE TABLE population(transaction_id TEXT PRIMARY KEY,event_time TEXT,day TEXT,amount_cents INTEGER,hgb_score REAL,rule_match INTEGER,tie TEXT)")
        db.executemany("INSERT INTO population VALUES (?,?,?,?,?,?,?)",[(r["transaction_id"],r["event_time"],r["day"],r["amount_cents"],r["hgb_score"],int(r["rule_match"]),r["tie"]) for day in sorted(days) for r in days[day]])
    with closing(sqlite3.connect(plan_path)) as db,db:
        db.execute("CREATE TABLE reviews(budget INTEGER,policy TEXT,transaction_id TEXT,day TEXT,rank INTEGER,PRIMARY KEY(budget,policy,transaction_id),UNIQUE(budget,policy,day,rank))")
        for budget in protocol["budgets"]:
            for policy in protocol["policies"]:
                for day,rows in sorted(days.items()):
                    selected=allocate_day(rows,budget,policy)
                    db.executemany("INSERT INTO reviews VALUES (?,?,?,?,?)",[(budget,policy,r["transaction_id"],day,index) for index,r in enumerate(selected,1)])
        baseline={row[0] for row in db.execute("SELECT transaction_id FROM reviews WHERE budget=10 AND policy='hgb'")}
    old={row["transaction_id"] for row in (json.loads(line) for line in Path(protocol["inputs"]["baseline_queue"]["path"]).open(encoding="utf-8")) if row["strategy"]=="hist_gradient_boosting"}
    if baseline!=old: raise ValueError("HGB top-10 list differs from original baseline")
    manifest={"status":"label_free_plans_frozen","protocol_sha256":sha256_file(directory / "protocol.json"),"population_sha256":sha256_file(population_path),
        "plans_sha256":sha256_file(plan_path),"rows":sum(map(len,days.values())),"days":len(days),"labels_read":False,"baseline_top10_matches_original":True}
    write_json(directory / "plan-manifest.json",manifest)
    return manifest


def evaluate(root,directory):
    protocol=load_protocol(root,directory)
    manifest=json.loads((directory / "plan-manifest.json").read_text())
    if manifest["protocol_sha256"]!=sha256_file(directory / "protocol.json") or manifest["labels_read"] is not False: raise ValueError("Label-free plan freeze missing")
    if sha256_file(directory / "ranking-inputs.sqlite")!=manifest["population_sha256"] or sha256_file(directory / "planned-reviews.sqlite")!=manifest["plans_sha256"]: raise ValueError("Frozen plans changed")
    if (directory / "report.json").exists(): raise ValueError("Do not overwrite evaluated experiment")
    label_path=Path(protocol["labels_path"])
    if sha256_file(label_path)!=protocol["labels_sha256"]: raise ValueError("Evaluation labels changed")
    with closing(sqlite3.connect((directory / "ranking-inputs.sqlite").as_uri()+"?mode=ro",uri=True)) as db:
        population=dict(db.execute("SELECT transaction_id,day FROM population"))
    with closing(sqlite3.connect(label_path.as_uri()+"?mode=ro",uri=True)) as db:
        raw=dict(db.execute("SELECT transaction_id,is_fraud FROM labels WHERE split='validation'"))
    if set(raw)!=set(population) or any(type(v) is not int or v not in (0,1) for v in raw.values()): raise ValueError("Incomplete/bad development labels")
    labels={identity:bool(value) for identity,value in raw.items()}
    if sum(labels.values())!=protocol["expected_positives"]: raise ValueError("Positive count differs from frozen source")
    results={};monthly={}
    with closing(sqlite3.connect((directory / "planned-reviews.sqlite").as_uri()+"?mode=ro",uri=True)) as db:
        for budget in protocol["budgets"]:
            baseline={r[0] for r in db.execute("SELECT transaction_id FROM reviews WHERE budget=? AND policy='hgb'",(budget,))}
            for policy in protocol["policies"]:
                selected={r[0] for r in db.execute("SELECT transaction_id FROM reviews WHERE budget=? AND policy=?",(budget,policy))}
                key=f"{budget}/{policy}"
                results[key]=outcome_metrics(population,selected,baseline,labels,budget=budget)
                if budget==protocol["primary_budget"]:
                    monthly[policy]={}
                    for month in sorted({day[:7] for day in population.values()}):
                        subset={x:d for x,d in population.items() if d.startswith(month)}
                        monthly[policy][month]=outcome_metrics(subset,selected & subset.keys(),baseline & subset.keys(),{x:labels[x] for x in subset},budget=budget)
    if results["10/hgb"]["true_positives"]!=protocol["expected_baseline_top10_tp"]: raise ValueError("Original top-10 baseline differs")
    report={"status":"capacity_development_evaluated","protocol_sha256":sha256_file(directory / "protocol.json"),"plan_manifest_sha256":sha256_file(directory / "plan-manifest.json"),
        "data_kind":protocol["data_kind"],"metrics":results,"primary_monthly":monthly,"final_test_evaluated":False,"real_LLM_requests":0,"human_approved":False,"deployed":False,
        "recommendation":"Keep all candidates pending independent validation and human review; no automatic adoption based on development metrics.","limits":protocol["limits"]}
    write_json(directory / "report.json",report)
    return report
