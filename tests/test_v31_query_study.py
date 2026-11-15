from copy import deepcopy
import json

import pytest

from app.evaluation.v3_narratives import DEVELOPMENT
from app.evaluation.v31_query_benchmark import build, candidates, query_case
from app.evaluation.v31_query_narratives import QUERY_EVALUATION
from app.evaluation.v31_query_protocol import audit_case, load, prepare
from app.evaluation.v31_query_score import score_stage
from app.investigation.v3.delivery import request_for
from app.investigation.v3.rule_workflow import collect_rule_workflow
from app.investigation.v3.study_delivery import StudyDeliveryClient, StudyHybridReviewContract, StudyReviewContract
from app.investigation.v3.trajectory import run_case
from tests.test_v3_native_planner import read_call
from tests.test_v3_phase_store import probe_report
from tests.test_v3_source_access import start


def test_candidate_pack_and_schedule_are_separate_and_fingerprinted(tmp_path):
    directory=tmp_path/'dataset'
    manifest=build(directory)
    assert manifest['counts']=={'revised_v4_author_candidate':30,'new_budgeted_query_case':10}
    assert not set(DEVELOPMENT).intersection(c['family'] for c in manifest['cases'])
    protocol=prepare(directory,tmp_path/'preregistration')
    assert protocol['planned_case_runs']==480 and protocol['planned_stages']==1440
    assert protocol['gate3_ready'] is protocol['external_requests_authorized'] is False
    audit=load(tmp_path/'preregistration/reachability-audit.json')
    assert len(audit)==40 and sum(r['simulated_unavailable_queries'] for r in audit.values())==2
    snapshot=directory/manifest['cases'][0]['runtime']
    payload=load(snapshot);payload['records'][0]['text']='Changed'
    snapshot.write_text(json.dumps(payload),encoding='utf-8')
    with pytest.raises(ValueError,match='content changed'):
        prepare(directory,tmp_path/'tampered')


def test_reference_cannot_use_future_correction_in_earlier_stage():
    snapshot,reference=query_case(QUERY_EVALUATION[-1],9)
    later=reference['stages'][2]['full_state']['sources'][0]
    reference['stages'][1]['full_state']['sources']=[later]
    with pytest.raises(ValueError,match='future'):
        audit_case(snapshot,reference)


def test_stale_same_value_has_no_credit_and_unknown_is_not_a_semantic_error(tmp_path):
    snapshot,reference=next((s,r) for s,r in candidates() if r['family']=='delegation_boundary'
                            and r['stages'][1]['full_state']['values']==['denied'])
    _,_,source=start(tmp_path,snapshot)
    result=run_case(source,'B0')
    phase=result['phases'][2]
    correct_source=reference['stages'][2]['full_state']['sources'][0]
    stale_source=reference['stages'][1]['full_state']['sources'][0]
    report=deepcopy(phase['report'])
    customer=[f for f in report['facts'] if f['predicate']=='customer_authorization']
    current=next(f for f in customer if f['citations'][0]['source_id']==correct_source)
    stale=next(f for f in customer if f['citations'][0]['source_id']==stale_source)
    # Set the modeled interpretation explicitly so the test isolates source use
    # from the deterministic natural-language rule's performance.
    current['value']=stale['value']='denied'
    report['source_statement'].update(value='denied',fact_ids=[current['id']])
    assert score_stage(snapshot,reference,2,phase['evidence'],report)['full_information_target_correct']
    report['source_statement']['fact_ids']=[stale['id']]
    assert not score_stage(snapshot,reference,2,phase['evidence'],report)['full_information_target_correct']
    extra=deepcopy(current);extra.update(id='F99',predicate='customer_retraction')
    report['facts'].append(extra)
    score=score_stage(snapshot,reference,2,phase['evidence'],report)
    assert score['unscored_supported_fact_count']==1
    assert score['covered_contradicted_fact_count']==0


class ScriptedClient:
    simulation=True

    def chat(self,messages,tools=None):
        system=json.loads(messages[0]['content'])
        if tools:
            allowed=tools[0]['function']['parameters']['properties']['source_ids']['items']['enum']
            return read_call(allowed[:1])
        schema=system['schema']
        def observations(s):
            facts=[]
            for branch in s['properties']['facts']['items'].get('anyOf',[]):
                props=branch['properties'];predicate=props['predicate']['const']
                if predicate=='customer_retraction':continue
                item=dict(predicate=predicate,refs=[props['refs']['items']['enum'][0]])
                if 'value' in props:item['value']=props['value']['enum'][0]
                facts.append(item)
            return dict(facts=facts)
        report=probe_report()
        for action in report['investigation']['recommendations']:
            action.pop('action');action['action_code']='A1'
        report['source_statement']=dict(selected_fact_id=None,rationale='Scripted format test')
        if system['stage']=='extract':out=observations(schema)
        elif system['stage']=='extract_and_report':
            assert 'report_codebook' in json.loads(messages[1]['content'])
            out=dict(extraction=observations(schema['properties']['extraction']),report=report)
        else:out=report
        return dict(role='assistant',content=json.dumps(out))


@pytest.mark.parametrize(('condition','contract'),[
    ('B1',StudyReviewContract),('B2',StudyReviewContract),('B4',StudyHybridReviewContract),
])
def test_real_trajectory_entry_supports_common_report_contract(tmp_path,condition,contract):
    snapshot,_=next(candidates())
    _,_,source=start(tmp_path,snapshot)
    result=run_case(source,condition,lambda _:ScriptedClient(),report_contract_factory=contract)
    assert result['complete'] and all(p['report'] for p in result['phases']), [p['error'] for p in result['phases']]
    if condition=='B1':
        assert all(p['protocol_request_attempts']==1 for p in result['phases'])
    if condition=='B2':
        assert result['planning_protocol_attempts']>0
    if condition=='B4':
        assert result['planning_protocol_attempts']==0
        assert 'literal_machine_field' in result['phases'][0]['report']['observation_producers'].values()


def test_wire_routes_native_tools_and_composite_schema_without_mixing_contracts(tmp_path):
    snapshot,_=next(candidates())
    _,_,source=start(tmp_path,snapshot)
    acquired=collect_rule_workflow(source)
    contract=StudyReviewContract(acquired.target_id)
    schema=dict(type='object',properties=dict(report=contract.composite_schema_for(acquired)),
                additionalProperties=False)
    request=request_for('extract_and_report',schema,{})
    client=object.__new__(StudyDeliveryClient)
    client.model='zai-org/GLM-5.3-Flash'
    client.policy=dict(version=2,max_tokens=4096,thinking_budget=128,temperature=.2)
    wire=client.request_payload(**request)
    assert wire['response_format']['json_schema']['schema']==schema
    tools=[{'type':'function','function':{'name':'v3_read'}}]
    wire=client.request_payload(request['messages'],tools)
    assert wire['tools']==tools and 'response_format' not in wire
