"""Development fixtures and task scoring; never a fraud-label benchmark."""

from contextlib import closing
from datetime import datetime, timedelta
import json
from pathlib import Path
import sqlite3

from app.risk.case_snapshot import export_case
from app.risk.history import sha256_file


CHECK_ORDER = ("high_prior_frequency_1h", "amount_above_prior_positive_max_30d", "merchant_id_unseen_30d")


def build_development_pack(spec_path: Path, directory: Path):
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    if spec["split"] != "development_only" or spec["data_kind"] != "hand_constructed_synthetic_fixtures_NOT_IBM_samples":
        raise ValueError("Explicit fixture development provenance required")
    directory.mkdir(parents=True,exist_ok=False)
    cutoff = datetime.fromisoformat(spec["cutoff"])
    tasks = []
    for case in spec["cases"]:
        task_dir = directory / case["id"]
        task_dir.mkdir()
        source = task_dir / "fixture-source.sqlite"
        def row(identity,minutes,amount,merchant,user="fixture-user"):
            return (identity,len(rows)+1,user,"fixture-card",(cutoff+timedelta(minutes=minutes)).isoformat(),
                amount,merchant,"Chip","fixture-mcc","validation")
        rows = []
        rows.append(row("target",0,case["amount_cents"],case["merchant"]))
        for i,(minutes,amount,merchant) in enumerate(case["history"]):
            rows.append(row(f"past-{i}",minutes,amount,merchant))
        if case.get("excluded_records"):
            rows.append(row("future",1,999999,"secret-future"))
            rows.append(row("same-time-peer",0,999999,"secret-peer"))
            for i in range(5): rows.append(row(f"other-{i}",-i-1,999999,"secret-other","other-user"))
        with closing(sqlite3.connect(source)) as db, db:
            db.execute("""CREATE TABLE transactions(transaction_id TEXT PRIMARY KEY,source_row INTEGER,
                user_id TEXT,card_id TEXT,event_time TEXT,amount_cents INTEGER,merchant_id TEXT,channel TEXT,mcc TEXT,split TEXT)""")
            db.executemany("INSERT INTO transactions VALUES (?,?,?,?,?,?,?,?,?,?)",rows)
        manifest = export_case(task_dir / "case",source_database=source,expected_source_sha256=sha256_file(source),
            transaction_id="target",alert={"transaction_id":"target","score":0.8,"detector":"fixture-not-a-detector"},
            coverage_start=case.get("coverage_start","2017-12-01T00:00:00"),coverage_end="2018-03-01T00:00:00",
            full_source_scanned=True,source_report_sha256=sha256_file(spec_path))
        # Gold remains host-side. Investigators receive ONLY the case manifest/tools.
        tasks.append({"id":case["id"],"manifest":str(manifest),"gold":dict(zip(CHECK_ORDER,case["gold"])),
            "required_checks":case.get("required_checks",list(CHECK_ORDER))})
    return tasks


def score_task(result, *, gold, required_checks):
    """Compare against authored gold, independently of validator status computation."""
    findings = (result.get("findings") or {}).get("findings",[])
    predictions, invalid = {}, 0
    for finding in findings:
        check = finding.get("check")
        if check in predictions or check not in gold:
            invalid += 1
        else:
            predictions[check] = finding.get("assessment")
    correct = sum(predictions.get(check) == gold[check] for check in required_checks)
    wrong = invalid+sum(status != gold[check] for check,status in predictions.items())
    covered = sum(check in predictions for check in required_checks)
    passed = result.get("status") == "completed_validated" and correct == len(required_checks) and wrong == 0
    return {"task_passed":passed,"required_checks":len(required_checks),"covered_checks":covered,
        "correct_required_checks":correct,"wrong_or_unknown_claims":wrong,"submitted_checks":len(findings),
        "run_status":result.get("status"),"client_requests":result.get("client_requests",0),
        "tool_attempts":result.get("tool_attempts",0),"format_repair_attempted":result.get("format_repair",{}).get("attempted",False)}
