"""Export G5-A evidence and matched comparisons; never replace failed paid slots."""

from collections import Counter
from decimal import Decimal
import json
from pathlib import Path
import shutil
import sqlite3
import xml.etree.ElementTree as ET

from app.risk.history import sha256_file
from app.risk.review_store import ReviewStore
from scripts.verify_mixed_benchmark import verify_batch


def read(path): return json.loads(path.read_text(encoding="utf-8"))


def tests(path):
    row = ET.parse(path).getroot().find("testsuite")
    result = {key:int(row.attrib[key]) for key in ("tests","failures","errors","skipped")}
    if result['failures'] or result['errors']: raise ValueError("Failed tests")
    return result | {"seconds":float(row.attrib['time']),"sha256":sha256_file(path)}


def main():
    root=Path(__file__).resolve().parents[1]
    primary=root/'runs/mixed-stage-v1'; stress=root/'runs/mixed-sample-stress-v1'; snapshot=primary/'frozen-code'
    batches={}
    for key,pack,directory in [('offline_development',primary,primary/'offline-development'),('offline_evaluation',primary,primary/'offline-evaluation'),
                               ('live_development',primary,primary/'live-development'),('live_evaluation',primary,primary/'live-evaluation'),
                               ('offline_sample_stress',stress,stress/'offline'),('live_sample_stress',stress,stress/'live')]:
        proof=verify_batch(root,pack,directory,frozen_code_root=snapshot)
        report=read(directory/'report.json')
        batches[key]={"directory":str(directory.relative_to(root)).replace('\\','/'),"report_sha256":sha256_file(directory/'report.json'),
                      "verification_sha256":sha256_file(directory/'batch-verification.json'),"aggregate":report['aggregate'],"audit":proof}
    records=read(primary/'live-development/report.json')['records']+read(primary/'live-evaluation/report.json')['records']
    fixed={r['task_id']:r for r in records if r['arm']=='fixed'}
    summary={r['task_id']:r for r in records if r['arm']=='evidence_only'}
    agents=[r for r in records if r['arm']=='agent']
    passed=lambda r:r.get('proof',{}).get('task_passed',False)
    matched=[r for r in agents if passed(r)]
    pairs={"agent_only_pass":sum(passed(a) and not passed(summary[a['task_id']]) for a in agents),
           "summary_only_pass":sum(not passed(a) and passed(summary[a['task_id']]) for a in agents),
           "both_pass":sum(passed(a) and passed(summary[a['task_id']]) for a in agents),
           "both_fail":sum(not passed(a) and not passed(summary[a['task_id']]) for a in agents)}
    raw_live=records+read(stress/'live/report.json')['records']
    failures=[]
    for record in raw_live:
        if passed(record): continue
        path=Path(record['summary_path']); task=read(path); trace=read(path.parent/'trace.json')
        usage=read(path.parent/'usage.json') if (path.parent/'usage.json').exists() else {}
        failures.append({"task_id":record['task_id'],"arm":record['arm'],"primary":'sample_stress' not in record['task_id'],
                         "receipt_status":record['status'],"runtime_status":task['status'],"failure_stage":task['failure_stage'],
                         "provider_guard":usage.get('last_error_code'),"tool_attempts":task['tool_attempts'],
                         "summary_path":str(path).replace('\\','/'),"summary_sha256":sha256_file(path),
                         "raw_report_exists":(path.parent/'report.md').exists()})
    total_cost=sum((Decimal(batches[k]['aggregate'][arm]['estimated_cny']) for k in ('live_development','live_evaluation','live_sample_stress') for arm in ('fixed','evidence_only','agent')),Decimal(0))
    review=ReviewStore(root/'runs/reviews.sqlite'); candidate=review.current('case-467716-rule')
    with sqlite3.connect((root/'runs/llm-budget.sqlite').resolve().as_uri()+'?mode=ro',uri=True) as db:
        rows=db.execute('SELECT reserved_cny,estimated_cny,status FROM reservations').fetchall()
    example_source=primary/'live-development/runs/u02-full_counterevidence-agent/report.md'
    example=root/'examples/mixed-investigation.md';shutil.copyfile(example_source,example)
    result={"stage":"G5-A","date":"2026-10-22","status":"mixed_task_stage_accepted_with_negative_value_result",
            "experiment_commit":"6b89832","post_experiment_guard_fix_commit":"cf8c911","paid_rerun_after_guard_fix":False,
            "primary_contract_sha256":sha256_file(primary/'contract.json'),"secondary_contract_sha256":sha256_file(stress/'contract.json'),
            "full_regression":tests(root/'tmp/g5-tests.xml'),"clean_service_and_mixed_tests":tests(root/'tmp/g5-clean-tests.xml'),
            "batches":batches,"matched_successful_agent_tasks":{"tasks":len(matched),"agent_tool_attempts":sum(r['tool_attempts'] for r in matched),
                "fixed_tool_attempts_same_tasks":sum(fixed[r['task_id']]['tool_attempts'] for r in matched)},
            "paired_primary_agent_vs_summary":pairs,"failures":failures,
            "real_requests_this_stage":sum(r.get('real_requests') or 0 for r in raw_live),"estimated_cny_this_stage":str(total_cost),
            "project_ledger":{"entries":len(rows),"committed_estimate_cny":str(sum((Decimal(r[1] if r[1] is not None else r[0]) for r in rows),Decimal(0))),
                              "unknown_entries_including_prior":sum(r[2]!='estimated_from_usage' for r in rows)},
            "example":{"source":str(example_source.relative_to(root)).replace('\\','/'),"sha256":sha256_file(example)},
            "financial_candidate":{key:candidate[key] for key in ('candidate_id','version','status','deployed')},"review_audit":review.verify(),
            "remote_CI_executed":False,"final_2019_detector_test_opened":False,
            "decision":"No demonstrated advantage over question-aware deterministic workflow; keep numeric assessments in deterministic tools. Do not expand infrastructure to conceal this.",
            "limits":["32 primary tasks from 8 correlated users; evaluation has only 4 task users, not independent of detector training.",
                      "No inadequate-history/sample findings in the primary gold; gaps also tested in explicit fixtures and a separate two-case sample-policy stress.",
                      "1000-record stress threshold is intentionally strict, not an industry rule; it reuses two development users.",
                      "One frozen runtime emitted a numerically correct raw report after a denied seventh attempt. Batch audit failed it; current runtime regression prevents publication. Original scores unchanged.",
                      "Time/cost include local subprocess/validation overhead; no controlled production latency or actual billing claim.",
                      "Synthetic source, no fraud labels for task gold, no real-world business efficacy claim."]}
    output=root/'docs/mixed-stage-v1-results.json';output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({"output":str(output),"stage":result['stage'],"tests":result['full_regression']['tests'],"live_requests":result['real_requests_this_stage'],"estimated_cny":str(total_cost)},ensure_ascii=False))


if __name__=='__main__': main()
