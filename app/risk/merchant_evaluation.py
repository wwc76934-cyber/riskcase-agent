"""Authored development tasks; gold stays host-side and is not fraud ground truth."""

from datetime import datetime, timedelta
import json
from pathlib import Path
import sqlite3

from app.risk.case_snapshot import export_case
from app.risk.history import sha256_file
from app.risk.merchant_research import MerchantQuestion


def build_merchant_pack(spec_path: Path,directory: Path):
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    if spec["split"]!="development_only" or spec["data_kind"]!="hand_constructed_synthetic_fixtures_NOT_IBM_samples":
        raise ValueError("Explicit authored development provenance required")
    MerchantQuestion(**spec["question"])
    ids = [case["id"] for case in spec["cases"]]
    if len(set(ids))!=len(ids) or any(not identity or not all(c.isalnum() or c=="_" for c in identity) for identity in ids):
        raise ValueError("Unique safe task IDs required")
    directory.mkdir(parents=True,exist_ok=False)
    cutoff = datetime.fromisoformat(spec["cutoff"])
    tasks = []
    for case in spec["cases"]:
        task = directory / case["id"]
        (task / "dataset").mkdir(parents=True)
        source = task / "dataset/facts.sqlite"
        rows = []
        def add(user,time,amount,merchant,identity=None):
            rows.append((identity or f"row-{len(rows)}",len(rows),user,"fixture-card",time.isoformat(),amount,merchant,"fixture-channel","fixture-mcc","validation"))
        for days,merchant,amount,count in case["groups"]:
            if type(days) is not int or not 1<=days<=30 or type(count) is not int or not 1<=count<=1000 or type(amount) is not int:
                raise ValueError("Bounded fixture groups required")
            for index in range(count): add("fixture-user",cutoff-timedelta(days=days,minutes=index),amount,merchant)
        add("fixture-user",cutoff,9999,"target-merchant","target")
        add("fixture-user",cutoff,999999,"same-time-excluded")
        add("fixture-user",cutoff+timedelta(minutes=1),999999,"future-excluded")
        add("other-user",cutoff-timedelta(days=1),999999,"other-excluded")
        with sqlite3.connect(source) as db:
            db.execute("CREATE TABLE transactions(transaction_id TEXT PRIMARY KEY,source_row INTEGER,user_id TEXT,card_id TEXT,event_time TEXT,amount_cents INTEGER,merchant_id TEXT,channel TEXT,mcc TEXT,split TEXT)")
            db.executemany("INSERT INTO transactions VALUES (?,?,?,?,?,?,?,?,?,?)",rows)
        report = task / "build-report.json"
        report.write_text(json.dumps({"data_kind":spec["data_kind"],"spec_sha256":sha256_file(spec_path),"artifact_sha256":{"dataset/facts.sqlite":sha256_file(source)}}),encoding="utf-8")
        manifest = export_case(task / "case",source_database=source,expected_source_sha256=sha256_file(source),
            transaction_id="target",alert={"transaction_id":"target","score":0.8,"detector":"fixture_NOT_detector"},
            coverage_start=case.get("coverage_start","2017-12-01T00:00:00"),coverage_end="2019-01-01T00:00:00",
            full_source_scanned=True,source_report_sha256=sha256_file(report))
        tasks.append({"id":case["id"],"manifest":str(manifest.resolve()),"cohort_report":str(report.resolve()),"gold":case["gold"],"question":spec["question"]})
    (directory / "tasks-host-only.json").write_text(json.dumps(tasks,indent=2)+"\n",encoding="utf-8")
    return tasks


def score_merchant(result,gold):
    findings = result.get("findings") or {}
    correct = sum(findings.get(key)==value for key,value in gold.items())
    first = result.get("first_attempt")
    first_passed = None if first is None else first["status"]=="completed_validated" and all((first.get("findings") or {}).get(key)==value for key,value in gold.items())
    return {"task_passed":result["status"]=="completed_validated" and correct==2,
        "correct_metrics":correct,"required_metrics":2,"run_status":result["status"],
        "first_attempt_passed":first_passed,"format_repair_attempted":result.get("format_repair",{}).get("attempted",False)}
