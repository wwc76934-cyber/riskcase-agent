import json

import httpx
import pytest

from app.evaluation.v3_pilot_ledger import PilotLedger
from app.evaluation.v3_typed_repair import checked, execute, gate_still_reachable, load, save, sha
from app.evaluation.v34_selection_trial import prepare as prepare_prior
from app.evaluation.v35_customer_trial import prepare
from app.investigation.v3.known_response_client import KnownResponseDeliveryClient
from app.llm.cost_ledger import BudgetExceeded
from tests.test_v3_phase_store import probe_report
from tests.test_v3_typed_repair import prepared


def test_unreachable_gate_stops_only_when_remaining_perfect_cases_cannot_pass():
    thresholds=dict(delivered_at_least=33,complete_cases_at_least=10,source_correct_T1_T2_at_least=20)
    def result(failed_stages):
        return dict(assessments=[dict(stage=i,assessment=dict(delivered=i not in failed_stages,
             full_information_target_correct=i not in failed_stages)) for i in range(3)])
    assert gate_still_reachable([result({0,1,2})],12,thresholds)
    assert not gate_still_reachable([result({0,1,2}),result({1})],12,thresholds)
    assert not gate_still_reachable([result({0}),result({0}),result({0})],12,thresholds)


def test_v35_new_grant_60_wire_calls_and_known_costs_with_real_local_mcp(tmp_path):
    _,book=prepared(tmp_path)
    previous=tmp_path/'v34'
    prepare_prior(tmp_path/'parent',tmp_path/'data',previous,book)
    out=tmp_path/'v35'
    proposal=prepare(previous,out)
    assert checked(out)['proposal_sha256']==sha(out/'proposal.json')
    assert proposal['max_requests']==60 and proposal['max_requests_per_case_run']==5
    assert proposal['known_usage_plus_one_full_unknown_case_upper_cny']=='2.558240'
    with pytest.raises(FileNotFoundError):
        execute(out,live=True,client_factory=lambda *_:pytest.fail('Unauthorized'))
    grant=dict(state='approved',grant_message='UNIT only',proposal_sha256=sha(out/'proposal.json'),
               max_estimated_cny='2.60',project_limit_cny='34')
    with pytest.raises(ValueError):
        PilotLedger(book,out/'proposal.json',dict(grant,max_estimated_cny='3'))
    save(out/'authorization.json',grant)
    fixture_cases={}
    for case in load(out/'prepared.json')['cases']:
        snapshot=load(out/case['runtime']);reference=load(out/case['reference'])
        fixture_cases[case['case_id']]=(snapshot['stage_times'],reference['stages'])
    calls=[];transports=[]
    def client_factory(ledger,run_id,policy):
        def respond(request):
            wire=json.loads(request.content)
            task=json.loads(wire['messages'][0]['content'])
            context=json.loads(wire['messages'][1]['content'])
            assert 'expected_source_statement' not in wire['messages'][1]['content']
            packet=context.get('acquired',context)
            times,reference=fixture_cases[packet['case_id']]
            phase=times.index(packet['investigation_as_of'])
            truth=reference[phase]
            calls.append((phase,task['stage'],len(request.content)))
            if task['stage']=='extract':
                values={o['source_id']:o['value'] for o in truth['expected_observations']
                        if o['predicate']=='customer_authorization'}
                facts=[]
                for branch in task['schema']['properties']['facts']['items'].get('anyOf',[]):
                    fields=branch['properties'];predicate=fields['predicate']['const']
                    if predicate!='customer_authorization':continue
                    ref=fields['refs']['items']['enum'][0]
                    identity=packet['read_line_handles'][ref]['source_id']
                    facts.append(dict(predicate=predicate,refs=[ref],value=values.get(identity,'unresolved')))
                payload=dict(facts=facts)
            else:
                payload=probe_report()
                for action in payload['investigation']['recommendations']:
                    action.pop('action');action['action_code']='A1'
                expected=truth['expected_source_statement']['value']
                selected=next((f for f,v in reversed(list(context['observations'].items()))
                               if v['predicate']=='customer_authorization' and v['value']==expected),None)
                payload['source_statement']=dict(selected_fact_id=selected,rationale='Scripted protocol fixture only')
            body=dict(model=policy['model'],choices=[dict(message=dict(role='assistant',content=json.dumps(payload)))],
                usage=dict(prompt_tokens=20,completion_tokens=20,total_tokens=40))
            return httpx.Response(200,json=body)
        transport=httpx.Client(transport=httpx.MockTransport(respond));transports.append(transport)
        return KnownResponseDeliveryClient('test-only-key',policy=policy,ledger=ledger,run_id=run_id,http_client=transport)
    try:
        result=execute(out,live=True,client_factory=client_factory)
    finally:
        for transport in transports:transport.close()
    assert result['complete'] and result['external_model_requests']==0 and result['development_gate_only'] is None
    assert (result['delivered'],result['complete_delivered_cases'],result['source_statement_correct_T1_T2'])==(36,12,24)
    assert len(calls)==60 and max(size for _,_,size in calls)<=proposal['max_input_bytes']
    assert [lane for phase,lane,_ in calls if phase==0]==['report']*12
    ledger=PilotLedger(book,out/'proposal.json',grant)
    with pytest.raises(BudgetExceeded):
        ledger.reserve(ledger.run_id(proposal['case_ids'][0],1,0),'0.01',run_budget='.50',project_budget='34',max_requests=2)
