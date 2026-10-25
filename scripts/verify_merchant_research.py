"""Independently aggregate source SQL, then check evidence and rendered report."""

import argparse
from contextlib import closing
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import sqlite3

from app.risk.case_snapshot import COLUMNS
from app.risk.history import sha256_file
from app.risk.merchant_research import MerchantQuestion, check_merchant_answer, render_merchant_report, validate_merchant_answer


def verify_answer_attempts(trace,summary,question):
    """Historical runs without attempt metadata remain readable; new runs bind both scores."""
    if "first_attempt" not in trace: return
    for key in ("first_attempt","format_repair","failure_stage","maximum_client_requests"):
        if trace[key]!=summary[key]: raise ValueError("Answer attempt metadata disagrees")
    limit = trace["maximum_client_requests"]
    if type(limit) is not int or not 1<=limit<=6 or trace["client_requests"]>limit:
        raise ValueError("Total request budget differs")
    answers = [message for message in trace["messages"] if message["role"]=="assistant" and not message.get("tool_calls")]
    if not answers: raise ValueError("Missing first answer")
    body,stage = check_merchant_answer(answers[0].get("content"),trace["evidence"],question)
    first = {"status":"completed_validated" if stage is None else "answer_rejected","failure_stage":stage,"findings":body}
    if first!=trace["first_attempt"]: raise ValueError("First attempt does not match original answer")
    repair = trace["format_repair"]
    if type(repair["attempted"]) is not bool or repair["maximum_attempts"]!=1: raise ValueError("Invalid correction policy")
    if repair["attempted"]:
        if stage!="answer_format" or len(answers)!=2 or repair["status"]!="validated": raise ValueError("Invalid correction transition")
        if trace["messages"][-2]["role"]!="user" or trace["messages"][-1]!=answers[-1]: raise ValueError("Correction messages differ")
    elif len(answers)!=1 or stage is not None: raise ValueError("Unexpected final answer history")
    body,stage = check_merchant_answer(answers[-1].get("content"),trace["evidence"],question)
    if stage is not None or body!=trace["findings"]: raise ValueError("Final attempt does not match report")


def verify(summary_path,cohort_report):
    directory = summary_path.parent
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if summary["status"]!="completed_validated": raise ValueError("Validated completed task required")
    required = {"case/manifest.json","case/facts.sqlite","trace.json","question.json","report.md","policy.json"}
    if not required <= set(summary["artifact_sha256"]): raise ValueError("Missing required artifact")
    for name,digest in summary["artifact_sha256"].items():
        path = (directory / name).resolve()
        if not path.is_relative_to(directory.resolve()) or sha256_file(path)!=digest: raise ValueError("Changed/outside artifact")
    manifest = json.loads((directory / "case/manifest.json").read_text(encoding="utf-8"))
    cohort = json.loads(cohort_report.read_text(encoding="utf-8"))
    source = cohort_report.parent / "dataset/facts.sqlite"
    if sha256_file(cohort_report)!=manifest["source_report_sha256"]: raise ValueError("Source report changed")
    if sha256_file(source)!=manifest["source_facts_sha256"] or manifest["source_facts_sha256"]!=cohort["artifact_sha256"]["dataset/facts.sqlite"]:
        raise ValueError("Source facts changed")
    if sha256_file(directory / "case/facts.sqlite")!=manifest["projection_sha256"]: raise ValueError("Projection changed")
    if not "2018-01-01"<=manifest["cutoff"]<"2019-01-01": raise ValueError("Development period required")
    question = MerchantQuestion(**json.loads((directory / "question.json").read_text(encoding="utf-8")))
    if question.__dict__!=summary["question"]: raise ValueError("Question contract changed")
    trace = json.loads((directory / "trace.json").read_text(encoding="utf-8"))
    if trace["status"]!=summary["status"] or trace["findings"]!=summary["findings"]: raise ValueError("Trace/summary disagree")
    if trace["tool_attempts"]!=len(trace["tool_trace"]) or trace["tool_attempts"]!=summary["tool_attempts"] or trace["client_requests"]!=summary["client_requests"]:
        raise ValueError("Attempt counts disagree")
    checked,observed = 0,{}
    with closing(sqlite3.connect(source.resolve().as_uri()+"?mode=ro",uri=True)) as db:
        db.row_factory = sqlite3.Row
        target = dict(db.execute(f"SELECT {','.join(COLUMNS)} FROM transactions WHERE transaction_id=? AND split='validation'",(manifest["target_transaction_id"],)).fetchone())
        if target["event_time"]!=manifest["cutoff"] or target["user_id"]!=manifest["user_id"]: raise ValueError("Target binding differs")
        originals = [dict(row) for row in db.execute(f"SELECT {','.join(COLUMNS)} FROM transactions WHERE user_id=? AND event_time>=? AND event_time<? ORDER BY event_time,source_row",
            (manifest["user_id"],manifest["history_start"],manifest["cutoff"]))]+[target]
        with closing(sqlite3.connect((directory / "case/facts.sqlite").resolve().as_uri()+"?mode=ro",uri=True)) as projection:
            projection.row_factory = sqlite3.Row
            if {row[0] for row in projection.execute("SELECT name FROM sqlite_master WHERE type='table'")}!={"transactions"}: raise ValueError("Unexpected table")
            if [row[1] for row in projection.execute("PRAGMA table_info(transactions)")]!=list(COLUMNS): raise ValueError("Unexpected fields")
            actual = [dict(row) for row in projection.execute("SELECT * FROM transactions")]
        if sorted(actual,key=lambda row:row["transaction_id"])!=sorted(originals,key=lambda row:row["transaction_id"]): raise ValueError("Source projection differs")
        for entry in trace["tool_trace"]:
            if not entry["result"]["ok"]: continue
            data = entry["result"]["data"]
            content = {key:value for key,value in data.items() if key!="evidence_id"}
            identity = "ev-"+hashlib.sha256(json.dumps(content,sort_keys=True,separators=(",",":")).encode()).hexdigest()[:24]
            if identity!=data["evidence_id"] or trace["evidence"].get(identity)!=data or entry["tool"]!=data["tool"]: raise ValueError("Evidence identity differs")
            observed[identity]=data
            if data["projection_sha256"]!=manifest["projection_sha256"] or data["currency"]!="unspecified" or data["time_basis"]!=manifest["time_basis"]:
                raise ValueError("Evidence provenance differs")
            if data["tool"]=="get_alert_transaction":
                if entry["arguments"]!={} or data["transaction"]!=target or data["alert"]!=manifest["alert"]: raise ValueError("Target differs")
            elif data["tool"]=="summarize_merchant_distribution":
                hours = data["lookback_hours"]
                if type(hours) is not int or hours not in (24,168,720) or entry["arguments"]!={"lookback_hours":hours}: raise ValueError("Invalid window")
                begin = (datetime.fromisoformat(manifest["cutoff"])-timedelta(hours=hours)).isoformat()
                complete = begin>=manifest["history_start"] and manifest["full_source_scanned"] is True
                if data["window_start_inclusive"]!=begin or data["window_end_exclusive"]!=manifest["cutoff"] or data["complete_within_source"]!=complete:
                    raise ValueError("Window coverage differs")
                predicate = "user_id=? AND event_time>=? AND event_time<?"
                params = (manifest["user_id"],max(begin,manifest["history_start"]),manifest["cutoff"])
                groups = [dict(row) for row in db.execute(f"SELECT merchant_id,COUNT(*) AS n, SUM(CASE WHEN amount_cents>0 THEN 1 ELSE 0 END) AS p_n, SUM(CASE WHEN amount_cents>0 THEN amount_cents ELSE 0 END) AS p_sum, SUM(CASE WHEN amount_cents<0 THEN 1 ELSE 0 END) AS neg_n, SUM(CASE WHEN amount_cents<0 THEN amount_cents ELSE 0 END) AS neg_sum, SUM(CASE WHEN amount_cents=0 THEN 1 ELSE 0 END) AS zero_n FROM transactions WHERE {predicate} GROUP BY merchant_id",params)]
                counts = sorted(groups,key=lambda row:(-row["n"],row["merchant_id"]))
                amounts = sorted((row for row in groups if row["p_n"]),key=lambda row:(-row["p_sum"],row["merchant_id"]))
                n,p = sum(row["n"] for row in groups),sum(row["p_sum"] for row in groups)
                expected = {"merchant_count":len(groups),"record_count":n,"prior_count":n,"count_denominator":n,
                    "positive_record_count":sum(row["p_n"] for row in groups),"positive_amount_sum_cents":p,"positive_amount_denominator_cents":p,
                    "negative_record_count":sum(row["neg_n"] for row in groups),"negative_amount_sum_cents":sum(row["neg_sum"] for row in groups),
                    "zero_record_count":sum(row["zero_n"] for row in groups),"top1_count_numerator":counts[0]["n"] if counts else 0,
                    "top1_positive_amount_cents":amounts[0]["p_sum"] if amounts else 0,
                    "top_by_count":[{"merchant_id":row["merchant_id"],"record_count":row["n"]} for row in counts[:3]],
                    "top_by_positive_amount":[{"merchant_id":row["merchant_id"],"positive_amount_cents":row["p_sum"]} for row in amounts[:3]],
                    "merchant_preview_truncated":len(groups)>3,"preview_truncated":n>3,
                    "incomplete_reasons":[] if complete else ["history_coverage_not_proven"]}
                for field,value in expected.items():
                    if data[field]!=value: raise ValueError(f"Independent SQL aggregation differs: {field}")
                preview = [dict(row) for row in db.execute(f"SELECT {','.join(COLUMNS)} FROM transactions WHERE {predicate} ORDER BY event_time,source_row LIMIT 3",params)]
                if data["preview"]!=preview: raise ValueError("Preview differs")
            else: raise ValueError("Unexpected successful tool for merchant task")
            checked+=1
    if observed!=trace["evidence"]: raise ValueError("Unobserved evidence")
    if "cache_hits" in summary:
        seen_queries={}
        for entry in trace["tool_trace"]:
            key=(entry["tool"],json.dumps(entry["arguments"],sort_keys=True))
            if entry.get("cache_hit",False) and (key not in seen_queries or seen_queries[key]!=entry["result"]):
                raise ValueError("Cached query lacks identical earlier successful observation")
            if entry["result"]["ok"]: seen_queries[key]=entry["result"]
        if summary["cache_hits"]!=sum(e.get("cache_hit",False) for e in trace["tool_trace"]) or summary["successful_query_executions"]!=sum(e["result"]["ok"] and not e.get("cache_hit",False) for e in trace["tool_trace"]):
            raise ValueError("Query execution/cache counts disagree")
    verify_answer_attempts(trace,summary,question)
    body = validate_merchant_answer(json.dumps(trace["findings"]),trace["evidence"],question)
    if render_merchant_report(body,trace["evidence"],question)!=(directory / "report.md").read_text(encoding="utf-8"): raise ValueError("Report differs")
    proof = {"status":"source_sql_verified","summary_sha256":sha256_file(summary_path),"observations_checked":checked,"projected_rows":len(originals),"labels_opened":False}
    (directory / "verification.json").write_text(json.dumps(proof,indent=2)+"\n",encoding="utf-8")
    return proof


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary",type=Path,required=True)
    parser.add_argument("--cohort-report",type=Path,default=root / "data/cohorts/cohort-3bfdbaab486f/build-report.json")
    args = parser.parse_args()
    print(json.dumps(verify(args.summary,args.cohort_report),indent=2))


if __name__ == "__main__": main()
