"""Label-blind source tasks, user-disjoint partition, SQL reference and frozen code."""

from contextlib import closing
from dataclasses import asdict
from datetime import datetime,timedelta
import hashlib
import json
from pathlib import Path
import sqlite3

from app.risk.case_snapshot import export_case
from app.risk.history import sha256_file
from app.risk.merchant_research import MerchantQuestion


CODE_FILES = ["app/risk/source_merchant_pack.py","app/cli/merchant_benchmark.py","app/cli/merchant_research.py",
    "app/risk/merchant_research.py","app/risk/merchant_stats.py","app/risk/merchant_evaluation.py",
    "app/tools/case_tools.py","app/risk/case_snapshot.py","app/risk/investigation.py","app/agent/react_agent.py",
    "app/llm/siliconflow_client.py","app/llm/glm_client.py","app/llm/cost_ledger.py","scripts/verify_merchant_research.py"]


def write_json(path,value):
    temporary=path.with_suffix(path.suffix+".tmp")
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    temporary.replace(path)


def select_users(rows,*,count=12,seed="source-merchant-v1",excluded=("38",)):
    first={}
    for row in sorted(rows,key=lambda row:(row["event_time"],row["transaction_id"])):
        if row["user_id"] not in excluded: first.setdefault(row["user_id"],row)
    ordered=sorted(first,key=lambda user:hashlib.sha256((seed+":"+user).encode()).hexdigest())
    if len(ordered)<count: raise ValueError("Not enough distinct queue users")
    return [first[user] for user in ordered[:count]]


def sql_reference(db,manifest,question):
    """Independent SQL groups and rational comparison; never opens a label store."""
    windows=[]
    for hours in (question.short_hours,question.long_hours):
        begin=(datetime.fromisoformat(manifest["cutoff"])-timedelta(hours=hours)).isoformat()
        groups=db.execute("SELECT COUNT(*),SUM(CASE WHEN amount_cents>0 THEN 1 ELSE 0 END),SUM(CASE WHEN amount_cents>0 THEN amount_cents ELSE 0 END) FROM transactions WHERE user_id=? AND event_time>=? AND event_time<? GROUP BY merchant_id",
            (manifest["user_id"],max(begin,manifest["history_start"]),manifest["cutoff"])).fetchall()
        windows.append({"complete":begin>=manifest["history_start"] and manifest["full_source_scanned"] is True,
            "count":sum(g[0] for g in groups),"positive_count":sum(g[1] for g in groups),"amount":sum(g[2] for g in groups),
            "top_count":max((g[0] for g in groups),default=0),"top_amount":max((g[2] for g in groups),default=0)})
    short,long=windows
    gold={}
    for key,numerator,denominator,sample in [("count_top1","top_count","count","count"),("positive_amount_top1","top_amount","amount","positive_count")]:
        if any(not w["complete"] or w[sample]<question.min_records or w[denominator]<=0 for w in windows): gold[key]="insufficient_evidence"
        else:
            difference=short[numerator]*long[denominator]-long[numerator]*short[denominator]
            gold[key]="increased" if difference>0 else "decreased" if difference<0 else "unchanged"
    return gold


def build_source_pack(root,directory,*,count=12):
    if type(count) is not int or count<4 or count%2: raise ValueError("An even number of at least four users required")
    detector=root / "runs/ml-v1-90f95014b365/report.json"
    report=json.loads(detector.read_text(encoding="utf-8"))
    queue=detector.parent / "daily-review.jsonl"
    if report["final_test_read"] or sha256_file(queue)!=report["artifact_sha256"][queue.name]: raise ValueError("Frozen development queue required")
    cohort_path=root / "data/cohorts/cohort-3bfdbaab486f/build-report.json"
    cohort=json.loads(cohort_path.read_text(encoding="utf-8"))
    source=cohort_path.parent / "dataset/facts.sqlite"
    if sha256_file(source)!=cohort["artifact_sha256"]["dataset/facts.sqlite"]: raise ValueError("Source changed")
    if not cohort["source_csv_fully_scanned"]: raise ValueError("Complete source scan required")
    rows=[]
    with closing(sqlite3.connect(source.resolve().as_uri()+"?mode=ro",uri=True)) as db:
        for line in queue.open(encoding="utf-8"):
            item=json.loads(line)
            if item["strategy"]!="hist_gradient_boosting": continue
            match=db.execute("SELECT user_id,event_time FROM transactions WHERE transaction_id=? AND split='validation' AND event_time>='2018-01-01' AND event_time<'2019-01-01'",(item["transaction_id"],)).fetchone()
            if match is None or match[1]!=item["event_time"]: raise ValueError("Queue/source target differs")
            rows.append({key:item[key] for key in ("transaction_id","event_time","score","day_rank")} | {"user_id":match[0]})
        selected=select_users(rows,count=count)
        directory.mkdir(parents=True,exist_ok=False)
        tasks=[]
        for index,item in enumerate(selected):
            split="development" if index<count//2 else "evaluation"
            case_dir=directory / "cases" / f"user-{index:02d}"
            manifest_path=export_case(case_dir,source_database=source,expected_source_sha256=cohort["artifact_sha256"]["dataset/facts.sqlite"],
                transaction_id=item["transaction_id"],alert={"transaction_id":item["transaction_id"],"score":item["score"],"day_rank":item["day_rank"],
                    "detector_report_sha256":sha256_file(detector),"selection":"Label-blind earliest queued alert per user, seeded user hash order; user 38 excluded."},
                coverage_start=cohort["coverage"]["warmup_start"],coverage_end=cohort["coverage"]["end"],full_source_scanned=True,source_report_sha256=sha256_file(cohort_path))
            manifest=json.loads(manifest_path.read_text())
            for short,long in ((168,720),(24,168)):
                question=MerchantQuestion(short,long,10)
                tasks.append({"id":f"u{index:02d}-{short}-{long}","split":split,"user_id":item["user_id"],"transaction_id":item["transaction_id"],
                    "manifest":str(manifest_path.resolve()),"manifest_sha256":sha256_file(manifest_path),"projection_sha256":manifest["projection_sha256"],
                    "question":asdict(question),"gold":sql_reference(db,manifest,question)})
    write_json(directory / "tasks-host-only.json",tasks)
    policy=root / "config/siliconflow-live-v1.json"
    contract={"version":1,"data_kind":"IBM_TabFormer_synthetic","selection_seed":"source-merchant-v1","users":count,"tasks":len(tasks),
        "cohort_report":str(cohort_path.resolve()),"cohort_report_sha256":sha256_file(cohort_path),"queue_sha256":sha256_file(queue),
        "tasks_sha256":sha256_file(directory / "tasks-host-only.json"),"policy_sha256":sha256_file(policy),
        "code_file_sha256":{name:sha256_file(root / name) for name in CODE_FILES},
        "arms":["fixed_scripted","evidence_only","agent"],"per_run_max_requests":6,"format_correction_maximum":1,
        "limits":["24 correlated questions from 12 users, not 24 independent users.","Evaluation users are disjoint from development task users, not from detector training users.",
            "Both partitions use 2018 development-period transactions; 2019 detector final test remains sealed.","Task references are SQL-derived descriptive comparisons, not fraud labels.","No prompt tuning after contract freeze; no automatic rerun of failed or interrupted slots."]}
    write_json(directory / "contract.json",contract)
    return contract


def load_frozen_pack(root,directory):
    contract=json.loads((directory / "contract.json").read_text(encoding="utf-8"))
    if sha256_file(directory / "tasks-host-only.json")!=contract["tasks_sha256"]: raise ValueError("Task pack changed")
    if sha256_file(root / "config/siliconflow-live-v1.json")!=contract["policy_sha256"]: raise ValueError("Policy changed after freeze")
    for name,digest in contract["code_file_sha256"].items():
        if sha256_file(root / name)!=digest: raise ValueError("Code changed after freeze: "+name)
    tasks=json.loads((directory / "tasks-host-only.json").read_text(encoding="utf-8"))
    groups={split:{t["user_id"] for t in tasks if t["split"]==split} for split in ("development","evaluation")}
    if groups["development"] & groups["evaluation"]: raise ValueError("Cross-partition user leakage")
    for task in tasks:
        path=Path(task["manifest"])
        if not path.resolve().is_relative_to(directory.resolve()) or sha256_file(path)!=task["manifest_sha256"] or sha256_file(path.parent / "facts.sqlite")!=task["projection_sha256"]:
            raise ValueError("Case artifact changed or outside pack")
    return contract,tasks
