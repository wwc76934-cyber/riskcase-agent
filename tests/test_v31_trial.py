import json
import httpx
import pytest
from tests.test_v3_typed_repair import prepared
from tests.test_v3_phase_store import probe_report
from app.evaluation.v31_trial import prepare
from app.evaluation.v3_typed_repair import execute,load,save,sha
from app.llm.siliconflow_client import SiliconFlowClient
from app.investigation.v3.v31_output import V31ReviewContract
from app.investigation.v3.report_plan import ReportPlanContract

@pytest.mark.parametrize('report_contract_factory',[V31ReviewContract,ReportPlanContract])
def test_v31_two_repeats_are_distinct_and_old_scope_cannot_authorize_them(tmp_path,report_contract_factory):
    _,book=prepared(tmp_path);out=tmp_path/'v31';p=prepare(tmp_path/'parent',tmp_path/'data',out,book,report_contract_factory=report_contract_factory)
    assert p['max_requests']==72 and p['repeats']==2 and p['thresholds']['delivered_at_least']==33
    assert not (out/'authorization.json').exists() and p['largest_preflight_context_bound_bytes']<=20000
    save(out/'authorization.json',dict(state='approved',grant_message='UNIT TEST ONLY, not actual consent',proposal_sha256=sha(out/'proposal.json'),max_estimated_cny=p['max_estimated_cny'],project_limit_cny='34'))
    clients=[];requests=[]
    def factory(ledger,run,policy):
        def transport(request):
            body=json.loads(request.content);requests.append(body);system=json.loads(body['messages'][0]['content'])
            if system['stage']=='extract':
                facts=[]
                for branch in system['schema']['properties']['facts']['items'].get('anyOf',[]):
                    props=branch['properties'];fact=dict(predicate=props['predicate']['const'],refs=[props['refs']['items']['enum'][0]])
                    if 'value' in props:fact['value']='success' if fact['predicate']=='authentication' else 'unresolved'
                    facts.append(fact)
                payload=dict(facts=facts)
            else:
                payload=probe_report()
                if report_contract_factory is ReportPlanContract:
                    for step in payload['investigation']['recommendations']:
                        step.pop('action');step['action_code']='A1'
            return httpx.Response(200,json=dict(model=policy['model'],choices=[dict(message=dict(role='assistant',content=json.dumps(payload)))],usage=dict(prompt_tokens=20,completion_tokens=20,total_tokens=40)))
        client=httpx.Client(transport=httpx.MockTransport(transport));clients.append(client)
        return SiliconFlowClient('unit-key-not-real',policy=policy,ledger=ledger,run_id=run,http_client=client)
    try:
        result=execute(out,live=True,client_factory=factory)
        assert result['complete'] and result['case_runs_recorded']==12 and result['recorded_phases']==36 and result['delivered']==36
        assert len(requests)==72 and result['external_model_requests']==0 and result['development_gate_only'] is None
        assert len(list((out/'live/cases').glob('*-r1.json')))==6 and len(list((out/'live/cases').glob('*-r2.json')))==6
        assert not any('expected_source_statement' in json.dumps(r) for r in requests)
    finally:
        for c in clients:c.close()
