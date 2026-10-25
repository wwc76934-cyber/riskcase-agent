"""Check a local case projection and observations against independent source SQL."""

import argparse
from contextlib import closing
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import sqlite3

from app.risk.case_snapshot import COLUMNS
from app.risk.history import sha256_file
from app.risk.investigation import validate_findings, render_report


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--cohort-report", type=Path, default=root / "data/cohorts/cohort-3bfdbaab486f/build-report.json")
    args = parser.parse_args()
    run = args.summary.parent
    summary = json.loads(args.summary.read_text(encoding="utf-8"))
    if summary["status"] != "completed_validated":
        raise ValueError("Completed locally validated case required")
    for name, digest in summary["artifact_sha256"].items():
        if sha256_file(run / name) != digest:
            raise ValueError(f"Case artifact changed: {name}")
    manifest = json.loads((run / "case/manifest.json").read_text(encoding="utf-8"))
    if sha256_file(args.cohort_report) != manifest["source_report_sha256"]:
        raise ValueError("Source report changed")
    cohort = json.loads(args.cohort_report.read_text(encoding="utf-8"))
    source = args.cohort_report.parent / "dataset/facts.sqlite"
    if sha256_file(source) != manifest["source_facts_sha256"] or manifest["source_facts_sha256"] != cohort["artifact_sha256"]["dataset/facts.sqlite"]:
        raise ValueError("Source facts changed")
    if not "2018-01-01" <= manifest["cutoff"] < "2019-01-01":
        raise ValueError("Case cutoff outside development period")
    with closing(sqlite3.connect(source.resolve().as_uri()+"?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        target = dict(db.execute(f"SELECT {','.join(COLUMNS)} FROM transactions WHERE transaction_id=? AND split='validation'",
            (manifest["target_transaction_id"],)).fetchone())
        originals = [dict(row) for row in db.execute(f"SELECT {','.join(COLUMNS)} FROM transactions "
            "WHERE user_id=? AND event_time>=? AND event_time<? ORDER BY event_time,source_row",
            (manifest["user_id"], manifest["history_start"], manifest["cutoff"]))]+[target]
        with closing(sqlite3.connect((run / "case/facts.sqlite").resolve().as_uri()+"?mode=ro", uri=True)) as projected:
            projected.row_factory = sqlite3.Row
            tables = {row[0] for row in projected.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if tables != {"transactions"}:
                raise ValueError("Unexpected table in case projection")
            columns = [row[1] for row in projected.execute("PRAGMA table_info(transactions)")]
            if columns != list(COLUMNS):
                raise ValueError("Unexpected case fields")
            actual = [dict(row) for row in projected.execute("SELECT * FROM transactions")]
        if sorted(actual, key=lambda row: row["transaction_id"]) != sorted(originals, key=lambda row: row["transaction_id"]):
            raise ValueError("Projection does not match exact allowed source rows")
        trace = json.loads((run / "trace.json").read_text(encoding="utf-8"))
        if trace["tool_attempts"] != summary["tool_attempts"] or len(trace["tool_trace"]) != trace["tool_attempts"]:
            raise ValueError("Tool attempt counts disagree")
        checked = 0
        for entry in trace["tool_trace"]:
            if not entry["result"]["ok"]:
                continue
            data = entry["result"]["data"]
            identity = data["evidence_id"]
            content = {key:value for key,value in data.items() if key!="evidence_id"}
            expected = "ev-"+hashlib.sha256(json.dumps(content,sort_keys=True,separators=(",",":")).encode()).hexdigest()[:24]
            if identity != expected or trace["evidence"].get(identity) != data:
                raise ValueError("Evidence registry/content fingerprint disagrees")
            if data["tool"] == "get_alert_transaction":
                if data["transaction"] != target or data["alert"] != manifest["alert"]:
                    raise ValueError("Target observation disagrees")
            else:
                hours = entry["arguments"]["lookback_hours"]
                start = (datetime.fromisoformat(manifest["cutoff"])-timedelta(hours=hours)).isoformat()
                if data["lookback_hours"] != hours or data["window_start_inclusive"] != start or data["window_end_exclusive"] != manifest["cutoff"]:
                    raise ValueError("Observation window differs from bound query")
                bounds = (manifest["user_id"], data["window_start_inclusive"], manifest["cutoff"])
                expected_complete = data["window_start_inclusive"] >= manifest["history_start"] and manifest["full_source_scanned"]
                # Include projection start as a separate bound for incomplete windows.
                predicate = "user_id=? AND event_time>=? AND event_time<? AND event_time>=?"
                params = (*bounds,manifest["history_start"])
                count, amount, positive_max = db.execute(f"SELECT COUNT(*),COALESCE(SUM(amount_cents),0),MAX(CASE WHEN amount_cents>0 THEN amount_cents END) FROM transactions WHERE {predicate}",params).fetchone()
                if data["prior_count"] != count or data["complete_within_source"] != expected_complete:
                    raise ValueError("Independent history count/coverage disagrees")
                if data["tool"] == "summarize_history":
                    if data["amount_sum_cents"] != amount or data["positive_amount_max_cents"] != positive_max or data["target_amount_cents"] != target["amount_cents"]:
                        raise ValueError("Independent money aggregation disagrees")
                    row_predicate, row_params = predicate, params
                else:
                    row_predicate, row_params = predicate+" AND merchant_id=?", (*params,target["merchant_id"])
                    merchant_count = db.execute(f"SELECT COUNT(*) FROM transactions WHERE {row_predicate}",row_params).fetchone()[0]
                    if data["merchant_prior_count"] != merchant_count:
                        raise ValueError("Independent merchant count disagrees")
                previews = [dict(row) for row in db.execute(f"SELECT {','.join(COLUMNS)} FROM transactions WHERE {row_predicate} ORDER BY event_time,source_row LIMIT 3",row_params)]
                if data["preview"] != previews:
                    raise ValueError("Evidence preview differs from source")
            checked += 1
        body = validate_findings(json.dumps(trace["findings"]), trace["evidence"])
        if body != summary["findings"] or render_report(body,trace["evidence"]) != (run / "report.md").read_text(encoding="utf-8"):
            raise ValueError("Rendered report differs from checked evidence")
    result = {"status":"passed", "summary_sha256":sha256_file(args.summary),
        "verification_script_sha256":sha256_file(Path(__file__)), "projection_rows_verified":len(actual),
        "source_sql_observations_verified":checked, "label_database_opened":False,
        "final_test_cases_evaluated":False, "client_mode":summary["client_mode"],
        "limits":"Exact projection/facts/citations consistency, not LLM intelligence, task accuracy or fraud ground truth."}
    (run / "verification.json").write_text(json.dumps(result,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(result,indent=2))


if __name__ == "__main__":
    main()
