"""Freeze a proposed comparison only after version-bound human review.

No clients, credentials, execution authorization or result-based plan changes.
Memory remains gated and is not a condition of this first comparison.
"""
from pathlib import Path
import hashlib,json,random
from app.evaluation.v3_human_review import validate

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def load(path):return json.loads(Path(path).read_text(encoding='utf-8'))


def prepare(dataset,review_pack,review_response,output,*,repeats=3,seed=20261102):
    dataset,review_pack,review_response,output=map(Path,(dataset,review_pack,review_response,output))
    if type(repeats) is not int or repeats<2 or repeats>5:raise ValueError('Predeclare two to five repeats')
    if output.exists():raise FileExistsError('Protocol and results must not be overwritten')
    review=validate(review_pack,review_response)
    fingerprint=sha(dataset/'dataset.json')
    if review['dataset_sha256']!=fingerprint or fingerprint!=load(dataset/'dataset-sha256.json')['sha256']:raise ValueError('Reviewed dataset changed')
    manifest=load(dataset/'dataset.json');entries=[e for e in manifest['entries'] if e['fold']=='evaluation']
    packet=load(review_pack/'packet.json');reviewed={c['case_id']:c for c in packet['cases']}
    if len(entries)<40 or set(reviewed)!={e['case_id'] for e in entries}:raise ValueError('All independently reviewed candidates required')
    for e in entries:
        for key in ('runtime','reference'):
            directory='runtime' if key=='runtime' else 'controller';path=dataset/e[key]
            if path.resolve().parent!=(dataset/directory).resolve() or sha(path)!=e[key+'_sha256'] or reviewed[e['case_id']][key+'_sha256']!=e[key+'_sha256']:raise ValueError('Reviewed input changed')
    conditions=['B0','B1','B1_staged','B2','B4'];cases=[dict(case_id=e['case_id'],family=e['family'],runtime_sha256=e['runtime_sha256'],reference_sha256=e['reference_sha256']) for e in entries]
    schedule=[dict(case_id=e['case_id'],condition=c,repeat=r) for e in entries for r in range(1,repeats+1) for c in conditions]
    random.Random(seed).shuffle(schedule)
    body=dict(schema='v3-comparison-protocol-v1',state='frozen_proposal_NOT_execution_authority',dataset_sha256=fingerprint,
        review=review,cases=cases,conditions=conditions,repeats=repeats,seed=seed,schedule=schedule,
        primary_metric='strict_trajectory',primary_contrasts=[['B4','B0'],['B2','B1']],formatter_sensitivity_contrast=['B2','B1_staged'],
        condition_contracts=dict(B0='Declared strong deterministic acquisition/semantic rules, zero model requests',B1='Full currently acquired originals; one combined extraction/report call with at most one known-protocol repair',
            B1_staged='Same acquisition rights and full originals, typed extraction then report',B2='Native dynamic tool selection and two final delivery slots',B4='Deterministic literal authentication plus LLM customer semantics/conflict analysis'),
        acquisition=dict(equal_source_permissions=True,current_stage_only=True,free_and_prerequisite_sources_visible_to_all=True,
            full_material_llm_gets_all_affordable_sources=True,cost_bounded_B1='One batch semantic choice among affordable originals after common free/prerequisite acquisition',
            source_costs_and_time='Author simulation, not measured bank costs or production SLA'),
        metrics=['all_sample_delivery','strict_author_reference','conditional_business_quality_given_delivery','target_customer_statement_accuracy',
            'fact_accuracy_by_predicate','structural_citation_support','critical_acquisition','wrong_alert_close','unproven_fraud_confirmation',
            'final_T2_state','model_requests','tool_attempts','estimated_cost_and_unknown_holds','process_latency','recovery_count'],
        unmeasured_metrics=['Expert escalation necessity','Actual analyst quality or saved labor','Confirmed fraud or legal liability','Free-prose semantic correctness'],
        statistics=dict(paired_case_unit=True,repeat_runs_not_independent_cases=True,family_block_bootstrap=True,repeats_resampled_within_case=True,
            leave_one_family_out=True,nominal_interval_level=.95,small_family_limitations=True,population_coverage_claim=False,
            missing_or_incomplete_runs='Report planned denominators and stop status; no selected-success interval',multiplicity='Two prespecified primary contrasts; no omnibus Agent-win claim or unadjusted significance declaration'),
        memory=dict(enabled=False,reason='Gate 4 requires a stable Agent baseline and a separately frozen method experiment'),
        external_model_requests=0,paid_execution_authorized=False,agent_value_claim=False,
        code_sha256={str(p.relative_to(Path(__file__).resolve().parents[2])):sha(p) for folder in ('app/investigation/v3','app/evaluation','app/service') for p in sorted((Path(__file__).resolve().parents[2]/folder).glob('*.py'))})
    output.mkdir(parents=True)
    (output/'protocol.json').write_text(json.dumps(body,ensure_ascii=False,indent=2),encoding='utf-8')
    (output/'protocol-sha256.json').write_text(json.dumps(dict(sha256=sha(output/'protocol.json'))),encoding='utf-8')
    return body
