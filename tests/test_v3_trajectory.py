import json
import pytest
from tests.test_v3_source_access import runtime,start
from tests.test_v3_native_planner import read_call
from tests.test_v3_phase_store import probe_report
from app.investigation.v3.trajectory import run_case
from app.service.store import Blocked


class AdaptiveProtocolClient:
    """Scripted format/transport probe, intentionally not business inference."""
    simulation=True
    def __init__(self,fail=False):self.calls=0;self.fail=fail
    def chat(self,messages,tools=None):
        self.calls+=1
        if self.fail:raise RuntimeError('Scripted unknown execution status')
        system=json.loads(messages[0]['content'])
        if tools:
            context=json.loads(messages[1]['content']);catalog=context['catalog'];items={s['source_id']:s for s in catalog['sources']}
            allowed=tools[0]['function']['parameters']['properties']['source_ids']['items']['enum'];chosen=[];budget=catalog['remaining_cost_units'];minutes=catalog['remaining_minutes']
            for identity in sorted(allowed,key=lambda k:(items[k]['cost_units'],k)):
                record=items[identity]
                if record['cost_units']<=budget and record['latency_minutes']<=minutes:
                    chosen.append(identity);budget-=record['cost_units'];minutes-=record['latency_minutes']
            return read_call(chosen)
        schema=system['schema']
        def extraction(schema):
            facts=[]
            for branch in schema['properties']['facts']['items'].get('anyOf',[]):
                predicate=branch['properties']['predicate']['const'];handle=branch['properties']['refs']['items']['enum'][0]
                facts.append(dict(predicate=predicate,value='success' if predicate=='authentication' else 'unresolved',refs=[handle]))
            return dict(facts=facts)
        if system['stage']=='extract':payload=extraction(schema)
        elif system['stage']=='extract_and_report':payload=dict(extraction=extraction(schema['properties']['extraction']),report=probe_report())
        else:payload=probe_report()
        return dict(role='assistant',content=json.dumps(payload))
    def snapshot(self):return dict(simulated=True,requests=self.calls,estimated_cny='0')


@pytest.mark.parametrize('condition',['B0','B1','B1_staged','B2','B4'])
def test_all_conditions_share_time_permissions_budget_and_complete_three_stage_protocol(tmp_path,runtime,condition):
    _,_,source=start(tmp_path,runtime);clients=[]
    def factory(stage):
        assert condition!='B0';client=AdaptiveProtocolClient();clients.append(client);return client
    result=run_case(source,condition,factory)
    assert result['complete'] and len(result['phases'])==3 and all(p['report'] for p in result['phases'])
    assert all(p['as_of']==runtime['stage_times'][p['stage']] for p in result['phases'])
    assert all(p['protocol_request_attempts']<=6 for p in result['phases'])
    assert result['evidence_kind']=='scripted_protocol_probe' and not result['formal_agent_value_claim']
    assert source.position()['catalog']['remaining_cost_units']>=0
    if condition=='B4':assert clients[0].calls==1
    before=sum(c.calls for c in clients)
    again=run_case(source,condition,lambda stage:pytest.fail('Completed case must not create a new client'))
    assert again['phases']==result['phases'] and sum(c.calls for c in clients)==before


def test_unknown_model_call_stops_future_stages_and_stays_stopped_on_resume(tmp_path,runtime):
    _,_,source=start(tmp_path,runtime);client=AdaptiveProtocolClient(fail=True)
    result=run_case(source,'B2',lambda stage:client)
    assert not result['complete'] and result['blocked_uncertain_request'] and len(result['phases'])==1
    assert source.position()['stage']==0 and client.calls==1
    again=run_case(source,'B2',lambda stage:pytest.fail('Uncertain case cannot issue another request'))
    assert again['blocked_uncertain_request'] and client.calls==1


def test_memory_condition_cannot_start_before_a_stable_agent_baseline(tmp_path,runtime):
    _,_,source=start(tmp_path,runtime)
    with pytest.raises(Blocked,match='memory_gate_not_ready'):run_case(source,'B3',lambda stage:pytest.fail('No memory experiment authorized'))


def test_known_source_failure_is_not_a_model_protocol_error_or_unknown_fee(tmp_path,runtime):
    _,_,source=start(tmp_path,runtime);client=AdaptiveProtocolClient()
    class MissingSource:
        def __getattr__(self,name):return getattr(source,name)
        def call(self,*args,**kwargs):return dict(ok=False,error=dict(code='source_missing'))
    result=run_case(MissingSource(),'B1_staged',lambda stage:client)
    assert len(result['phases'])==3 and not result['blocked_uncertain_request'] and client.calls==0
    assert all(p['error']['type']=='SourceAccessFailure' and p['error']['code']=='source_missing' for p in result['phases'])
