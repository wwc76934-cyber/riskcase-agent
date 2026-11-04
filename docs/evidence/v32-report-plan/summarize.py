"""Read-only terminal-run evidence export; never rescore or repair responses."""
from pathlib import Path
from collections import Counter
from decimal import Decimal
import hashlib, json, sys
from jsonschema import Draft202012Validator

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
from app.evaluation.v3_fact_coverage import diagnose

def load(p): return json.loads(p.read_text(encoding='utf-8-sig'))
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,value):
    with p.open('x',encoding='utf-8') as stream:
        json.dump(value,stream,ensure_ascii=False,indent=2)

def main():
    scope=ROOT/'runs/v32-report-plan-proposal-v1'; live=scope/'live'
    summary_path=live/'recovery-summary-001.json'
    summary=load(summary_path)
    rows=[load(p) for p in sorted((live/'cases').glob('*.json'))]
    phases=[(r,p) for r in rows for p in r['runtime']['phases']]
    scored=[p for r in rows for p in r['assessments']]
    later=[p for p in scored if p['stage'] in (1,2)]
    assert len(rows)==summary['case_runs_recorded']
    assert len(phases)==summary['recorded_phases']
    assert sum(p['assessment']['delivered'] for p in scored)==summary['delivered']
    known=Decimal('0'); unknown=0; requests=0
    for p in scored:
        for e in p['ledger']['entries']:
            requests+=1
            if e['estimated_cny'] is None: unknown+=1
            else: known+=Decimal(e['estimated_cny'])
    assert requests==summary['external_model_requests']
    original=scope/'authentication-interruption-original'
    unchanged={str(p.relative_to(original)):sha(p)==sha(live/p.relative_to(original))
        for p in original.rglob('*') if p.is_file() and p.name!='jobs.sqlite'}
    assert unchanged and all(unchanged.values()), 'Original failure bytes changed'
    errors=[dict(case_id=r['case_id'],repeat=r['repeat'],stage=p['stage'],error=p['error'])
        for r,p in phases if p.get('error')]
    schema_errors=[]; response_counts=Counter(); raw_states=[]
    for path in sorted((live/'v3-model-records').glob('*/response-*.json')):
        request=load(path.with_name(path.name.replace('response-','request-')))
        system=json.loads(request['messages'][0]['content']); response_counts[system['stage']]+=1
        try: value=json.loads(load(path)['content'])
        except (ValueError,KeyError,TypeError):
            schema_errors.append(dict(path=str(path.relative_to(live)),stage=system['stage'],error='unparseable_json')); continue
        problems=list(Draft202012Validator(system['schema']).iter_errors(value))
        if problems:
            schema_errors.append(dict(path=str(path.relative_to(live)),stage=system['stage'],errors=[
                dict(path=list(e.absolute_path),rule=e.validator,message=e.message[:550]) for e in problems]))
        if system['stage']=='report':
            raw_states.append(dict(path=str(path.relative_to(live)),
                value=value.get('source_statement',{}).get('value'),schema_accepted=not problems,
                note='Raw enum only, not a business validation or accepted report'))
    coverage=[]
    for r,p in phases:
        coverage.append(dict(case_id=r['case_id'],repeat=r['repeat'],stage=p['stage'],diagnosis=diagnose(
            load(scope/'runtime'/(r['case_id']+'.json')),load(scope/'controller'/(r['case_id']+'.json')),
            p['stage'],p['evidence'],p['report'])))
    partition=Counter(f['status'] for row in coverage for f in row['diagnosis']['facts'])
    financial=summary['financial_snapshot']; latency=[p['process_elapsed_seconds_including_recovery'] for _,p in phases]
    body=dict(schema='v32-coded-report-development-result-v1',source_ci_commit='fdc0c1a81a6168fd8eb667ab25f8201ca16b27ea',
        proposal_sha256=sha(scope/'proposal.json'),grant_sha256=sha(scope/'authorization.json'),
        planned_case_runs=12,recorded_case_runs=len(rows),planned_stages=36,attempted_stages=len(phases),
        unattempted_stages=36-len(phases),delivered_stages=summary['delivered'],
        fully_delivered_case_runs=summary['complete_delivered_cases'],target_correct_T1_T2=summary['source_statement_correct_T1_T2'],
        target_T1_T2_denominator=24,delivered_later_stages=sum(p['assessment']['delivered'] for p in later),
        original_strict_author_reference_stages=sum(p['assessment']['strict_author_reference_pass'] for p in scored),
        original_strict_metric_has_reference_coverage_limitation=True,
        external_requests=requests,unknown_fee_requests=unknown,known_usage_estimated_cny=str(known),
        batch_committed_estimate_and_risk_hold_cny=financial['batch_committed_estimate_cny'],
        project_committed_estimate_and_risk_hold_cny=financial['project_committed_estimate_cny'],
        provider_actual_bill_known=False,response_counts=dict(response_counts),
        model_autonomous_tool_calls=0,host_source_tool_attempts=sum(p['source_tool_attempts'] for _,p in phases),
        delivered_wrong_alert_closes=sum(p['assessment']['wrong_alert_close'] for p in scored),
        delivered_unproven_fraud_confirmations=sum(p['assessment']['unproven_fraud_confirmation'] for p in scored),
        latency_seconds=dict(sum=sum(latency),minimum=min(latency),maximum=max(latency),not_analyst_time=True),
        development_thresholds=summary['development_thresholds'],stop_reason=summary['stop_reason'],
        supplemental_fact_coverage_partition=dict(partition),same_observed_development_cases=True,
        original_interruption_files_unchanged=unchanged,default_product='B0',
        formal_agent_value_proven=False,memory_value_proven=False)
    out=ROOT/'docs/evidence/v32-final-v1';out.mkdir(exist_ok=False)
    save(out/'summary.json',body);save(out/'failures.json',errors)
    save(out/'schema-diagnosis.json',schema_errors);save(out/'raw-report-enums.json',raw_states)
    save(out/'supplementary-fact-coverage.json',coverage)
    save(out/'source-fingerprints.json',dict(original_freeze=sha(scope/'freeze.json'),recovery_review=sha(scope/'recovery-review.json'),
        recovery_summary=sha(summary_path),original_interruption_database=sha(original/'jobs.sqlite'),
        files={str(p.relative_to(live)):sha(p) for p in live.rglob('*.json')}))
    print(json.dumps(body,ensure_ascii=False,indent=2))

if __name__=='__main__': main()
