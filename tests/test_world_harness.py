from copy import deepcopy
import json
import pytest
from tests.test_fraud_inquiry import inquiry_snapshot
from app.evaluation.fraud_world_data import make_case
from app.investigation.world_service import WorldService
from app.investigation.world_baseline import BaselineProbeClient
from app.investigation.harness import HarnessWorker
from app.investigation.fraud_world import verify,DraftError,update_plan
from app.investigation.tickets import Tickets,MockDestination
from app.service.store import Conflict


@pytest.mark.parametrize('strategy',['adaptive','all_materials'])
def test_world_real_mcp_shared_harness_and_semantic_approval(tmp_path,inquiry_snapshot,strategy):
    runtime,_=make_case(inquiry_snapshot,'auth_denial',1,'world-transport-fixture')
    service=WorldService(tmp_path/strategy,strategy=strategy)
    job,_=service.submit('probe',runtime,'Investigate authorization with source lines and uncertainty')
    row=HarnessWorker(service,client_factory=BaselineProbeClient).step()
    assert row['state']=='awaiting_review',row['reason']
    report=row['result'];state=service.store.load(job)
    assert report['client_kind']=='simulation'
    assert report['access_cost_units']>0 and report['access_cost_units']<=30
    assert report['turns']==(1 if strategy=='all_materials' else 4)
    assert report['validation']['semantic_validation'] is False
    assert any(t['kind']=='authentication_is_not_authorization' for t in report['answer']['tensions'])
    handshakes=json.loads((service.directory/job/'mcp-handshakes.json').read_text())
    assert all(v['server']['name'].startswith('fraud-world-') for v in handshakes.values())
    answer=deepcopy(report['answer']);answer['observations'][0]['citations'][0]['quote']='invented source text'
    with pytest.raises(DraftError):verify(answer,state['evidence'])
    tickets=Tickets(service.store,MockDestination(tmp_path/(strategy+'.sqlite')))
    draft=tickets.draft(job,'Review originals, hypotheses and the transaction-specific authorization issue')
    with pytest.raises(Conflict,match='semantic'):tickets.approve(job,draft['hash'])
    tickets.approve(job,draft['hash'],reviewed_claims=['observations','hypotheses','timeline','recommendations','missing'])


def test_hypothesis_updates_are_fenced_and_cannot_cite_unread_evidence():
    state={'evidence':{'E1':{}},'plan':{}}
    h=dict(id='H1',description='A provisional authorization hypothesis',status='open',support=[],counterevidence=[],unknowns=['Not established'])
    args=dict(revision=0,hypotheses=[h],next_sources=['authentication'],decision_note='Need the original before judging')
    assert update_plan(args,state)['revision']==1
    assert update_plan(args,state)['replayed'] is True
    changed=deepcopy(args);changed['decision_note']='Changed under stale revision'
    with pytest.raises(ValueError,match='revision'):update_plan(changed,state)
    changed['revision']=1;changed['hypotheses'][0]['support']=['E999']
    with pytest.raises(ValueError,match='unread'):update_plan(changed,state)
    changed['hypotheses'][0]['support']=[];changed['hypotheses'][0]['status']='supported'
    with pytest.raises(ValueError,match='status'):update_plan(changed,state)
    from app.investigation.fraud_world import SOURCE_TYPES
    complete=deepcopy(args);complete['revision']=1;complete['next_sources']=SOURCE_TYPES
    assert update_plan(complete,state)['revision']==2


def test_tool_conversation_and_host_event_materialization(tmp_path,inquiry_snapshot):
    from app.investigation.world_baseline import report_from_observations
    class ConversationProbe:
        simulation=True
        def chat(self,messages,tools=None):
            frame=json.loads(messages[1]['content']);names={t['function']['name'] for t in tools}
            if frame['hypothesis_revision']==0:return BaselineProbeClient().chat(messages,tools)
            assert 'inquiry_overview' not in names
            observations=frame['observations']+[json.loads(m['content']) for m in messages if m['role']=='tool']
            if len(messages)>2:
                assert messages[2]['role']=='assistant'
                assert {m['tool_call_id'] for m in messages if m['role']=='tool'}=={c['id'] for c in messages[2]['tool_calls']}
            def call(name,args):return dict(role='assistant',content=None,tool_calls=[dict(id='probe-'+name,type='function',function=dict(name=name,arguments=json.dumps(args)))])
            catalogs=[o for o in observations if o.get('source')=='investigation_catalog']
            if not catalogs:return call('world_sources',dict(source_type='authentication'))
            read=[o for o in observations if o.get('source')=='investigation_originals']
            if not read:
                assert 'world_read' in names
                return call('world_read',dict(source_id=catalogs[0]['data']['available'][0]['source_id']))
            if not any('B' in o.get('data',{}) for o in read):return call('world_materials',{})
            assert 'world_materials' not in names and 'world_read' not in names
            if frame['hypothesis_revision']<2:return BaselineProbeClient().chat(messages,tools)
            answer=report_from_observations(observations)
            answer['timeline'].reverse()
            for event in answer['timeline']:event['event_time']='1999-01-01T00:00:00'
            return dict(role='assistant',content=json.dumps(answer))
    runtime,_=make_case(inquiry_snapshot,'auth_denial',1,'conversation-probe')
    s=WorldService(tmp_path);job,_=s.submit('roundtrip',runtime,'Transport probe, not model planning evidence')
    row=HarnessWorker(s,client_factory=ConversationProbe).step()
    assert row['state']=='awaiting_review',row['reason']
    assert row['result']['client_kind']=='simulation'
    assert all(e['event_time']!='1999-01-01T00:00:00' for e in row['result']['answer']['timeline'])
    assert s.store.load(job)['proposed_answer']['timeline'][0]['event_time']=='1999-01-01T00:00:00'
    assert row['result']['host_timeline_materialization'][0]['changes']
