import json
import pytest
from tests.test_v3_source_access import runtime,start
from app.service.v3_sources import SourceAccess
from app.investigation.v3.native_planner import NativePlanner


class NativeClient:
    simulation=True
    def __init__(self,responses):self.responses=list(responses);self.calls=0;self.requests=[]
    def chat(self,messages,tools=None):
        self.calls+=1;self.requests.append(dict(messages=messages,tools=tools));return self.responses.pop(0)
    def snapshot(self):return dict(simulated=True,requests=self.calls,estimated_cny='0')


def read_call(ids):
    return dict(role='assistant',content=None,tool_calls=[dict(id='native-read-1',type='function',function=dict(name='v3_read',arguments=json.dumps(
        dict(source_ids=ids,purpose='Read current originals',hypothesis_to_test='Authentication does not establish customer intent'))))])


def test_native_tool_choice_reads_actual_sources_and_preserves_delivery_budget(tmp_path,runtime):
    _,_,source=start(tmp_path,runtime);ids=[s['source_id'] for s in source.position()['catalog']['sources']]
    client=NativeClient([read_call(ids)]);result=NativePlanner(source,0,client).run()
    assert client.calls==1 and result['model_requests']==1 and len(source.evidence())==2
    assert result['trace'][0]['hypothesis_to_test'] and result['stop_reason']=='no_feasible_unread_source'
    parameters=client.requests[0]['tools'][0]['function']['parameters']
    assert set(parameters['properties']['source_ids']['items']['enum'])==set(ids)
    assert 'operation_key' not in parameters['properties']


def test_disabled_tool_is_recorded_and_cannot_mutate_source_state(tmp_path,runtime):
    _,_,source=start(tmp_path,runtime);bad=read_call([]);bad['tool_calls'][0]['function']['name']='refund'
    client=NativeClient([bad,dict(role='assistant',content='Ready to report missing evidence')])
    result=NativePlanner(source,0,client).run()
    assert result['trace'][0]['error']=='invalid_tool_selection_or_arguments' and len(source.evidence())==1
    assert result['model_requests']==2 and source.position()['acquired_source_ids']==[]


def test_crash_after_tool_effect_replays_operation_without_model_or_source_reissue(tmp_path,runtime):
    now=[100];store,claim,source=start(tmp_path,runtime,clock=lambda:now[0]);ids=[s['source_id'] for s in source.position()['catalog']['sources']]
    first=NativeClient([read_call(ids)])
    def crash(kind,ordinal):
        if kind=='source_operation_completed':raise SystemExit('After source commit, before planner checkpoint')
    with pytest.raises(SystemExit):NativePlanner(source,0,first,hook=crash).run()
    now[0]=221;replacement=store.claim('new-owner',lease_seconds=120,deadline_seconds=600,mode='offline')
    restarted=SourceAccess(source.path,replacement,runtime,clock=lambda:now[0]);second=NativeClient([])
    result=NativePlanner(restarted,0,second).run()
    assert first.calls==1 and second.calls==0 and restarted.position()['tool_attempts']==1
    assert result['trace'][0]['operation_replayed'] and len(restarted.evidence())==2


def test_single_batch_baseline_uses_one_batch_and_does_not_hide_any_original(tmp_path,runtime):
    _,_,source=start(tmp_path,runtime);ids=[s['source_id'] for s in source.position()['catalog']['sources']]
    client=NativeClient([read_call(ids)]);result=NativePlanner(source,0,client,mode='single_batch').run()
    assert result['stop_reason']=='single_batch_acquired' and len(source.evidence()['E2']['result']['data']['B'])==len(ids)
