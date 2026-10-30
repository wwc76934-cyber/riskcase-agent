from copy import deepcopy
from pathlib import Path
import asyncio
import json
import pytest
from app.evaluation.scenarios import build,LIVE_IDS
from app.evaluation.engine import execute,ARMS
from app.evaluation.scoring import score
from app.evaluation.adapters import local_connect,LocalSession
from app.investigation.gateway import Gateway
from app.investigation.harness import InvestigationService,HarnessWorker
from app.investigation.knowledge import corpus,SCOPE,AS_OF
from app.investigation.experience import lesson
from app.service.store import digest

ROOT=Path(__file__).resolve().parents[1]


def knowledge():
    content=lesson(['coverage','settlement','merchant'])
    return dict(documents=corpus(),scope=SCOPE,as_of=AS_OF,memory=dict(release=1,release_hash='test_fixture',lesson=content|dict(expires_at=9999999999,content_hash=digest(content))))


def test_family_partitions_and_gold_isolation():
    cases=build();assert len(cases)==len({c['id'] for c in cases})==60
    families={split:{c['family'] for c in cases if c['split']==split} for split in ('development','regression','frozen_audit')}
    assert not families['development'] & families['regression'] and not families['regression'] & families['frozen_audit'] and not families['development'] & families['frozen_audit']
    assert all(sum(c['split']==s for c in cases)==20 for s in families)
    assert set(LIVE_IDS)<={c['id'] for c in cases if c['split']=='frozen_audit'}
    assert all('gold' not in c['snapshot'] and c['snapshot']['case_id']==c['id'] for c in cases)


@pytest.mark.parametrize('arm',ARMS)
def test_development_contracts_and_ablations(tmp_path,arm):
    case=build()[10]  # Development merchant case; no frozen-audit tuning.
    record=execute(case,arm,tmp_path/arm,knowledge=knowledge())
    assert record['score']['task_passed'],record
    assert record['client_kind']=='scripted_control_NOT_MODEL' and record['real_requests']==0
    requests=[json.loads(p.read_text(encoding='utf-8')) for p in sorted((tmp_path/arm).glob('request-*.json'))]
    assert requests and all('required_sources' not in json.dumps(r) and '"gold"' not in json.dumps(r) for r in requests)
    if arm=='no_skill':
        assert not record['active_skills'] and record['knowledge_queries']==1
        assert 'load_skill' not in json.dumps(requests) and 'Skill catalog' not in json.dumps(requests)
    if arm=='no_knowledge':
        assert record['active_skills'] and record['knowledge_queries']==0
        assert 'search_knowledge' not in json.dumps(requests) and 'M:release' not in json.dumps(requests)


def test_scorer_counts_rejected_proposal_and_failure(tmp_path):
    record=execute(build()[0],'fixed_code',tmp_path/'baseline',knowledge=knowledge())
    result=json.loads((tmp_path/'baseline/result.json').read_text(encoding='utf-8'))
    original=deepcopy(result);result['status']='failed';result['proposed_answer']=result.pop('answer')
    assert score(result,build()[0]['gold'])['proposed_verdict_correct']
    assert not score(result,build()[0]['gold'])['task_passed']
    result=deepcopy(original);result['answer']['evidence_ids'].append('M:release-1')
    assert not score(result,build()[0]['gold'])['references_valid']
    result=deepcopy(original);result['answer']['verdict']='normal_change'
    assert not score(result,build()[0]['gold'])['host_validation_passed']


def test_disabled_tools_cannot_be_invoked_and_offline_rejects_live_client(tmp_path):
    service=InvestigationService(tmp_path/'s',ROOT/'investigation_skills',profile='no_knowledge')
    case=build()[0];job,_=service.submit('reject-disabled',case['snapshot'],case['question'])
    class Attack:
        simulation=True
        def chat(self,*args,**kwargs):return dict(role='assistant',content=None,tool_calls=[dict(id='x',type='function',function=dict(name='search_knowledge',arguments='{"query":"history"}'))])
    row=HarnessWorker(service,client_factory=Attack,connection_factory=local_connect).step()
    assert row['state']=='blocked' and not service.store.load(job)['evidence']
    assert all(not o['ok'] for o in service.store.load(job)['observations'])
    job,_=service.submit('reject-live',case['snapshot'],case['question'])
    class NeverCalled:
        simulation=False
        closed=False
        def chat(self,*args,**kwargs):raise AssertionError('Must not reach network')
        def close(self):self.closed=True
    client=NeverCalled();row=HarnessWorker(service,client_factory=lambda:client,connection_factory=local_connect).step()
    assert row['state']=='failed' and client.closed


@pytest.mark.parametrize('fault',['timeout','oversized','wrong_scope','unknown_tool'])
def test_faults_deny_without_fact_evidence(fault):
    case=build()[0];session=LocalSession(case['snapshot'])
    async def run():
        original=session.call_tool
        async def broken(name,args):
            if fault=='timeout':await asyncio.sleep(.1)
            response=await original(name,args)
            if fault=='oversized':response.structuredContent['data']['extra']='X'*13000
            if fault=='wrong_scope':response.structuredContent['case_id']='other-case'
            return response
        session.call_tool=broken
        gateway=Gateway({'tx_overview':session},case['snapshot'],timeout=.01)
        value=await gateway.call('shell' if fault=='unknown_tool' else 'tx_overview','{}')
        assert not value['ok'] and not gateway.evidence
        assert value['error']==dict(timeout='timeout',oversized='invalid_result',wrong_scope='permission_denied',unknown_tool='permission_denied')[fault]
    asyncio.run(run())


@pytest.mark.parametrize('wrong',[False,True])
def test_open_planning_notes_remain_visible_but_cannot_override_fact_gate(tmp_path,wrong):
    from app.investigation.harness import SkillScriptedClient
    from app.investigation.runner import ScriptedInvestigator
    class LeavesNotes(SkillScriptedClient):
        def chat(self,messages,tools=None):
            response=super().chat(messages,tools)
            if response.get('tool_calls') and response['tool_calls'][0]['id']=='close-plan':
                frame=json.loads(messages[1]['content'])
                facts=[dict(role='tool',content=json.dumps(o)) for o in frame['observations'] if o.get('ok') and 'source' in o]
                response=ScriptedInvestigator().chat(facts,tools)
                if wrong:
                    answer=json.loads(response['content']);answer['verdict']='duplicate_record';response['content']=json.dumps(answer)
            return response
    case=build()[10];service=InvestigationService(tmp_path,ROOT/'investigation_skills')
    job,_=service.submit('retained-planning',case['snapshot'],case['question'])
    row=HarnessWorker(service,client_factory=LeavesNotes,connection_factory=local_connect).step();state=service.store.load(job)
    assert state['plan']['todo'] and state['proposed_answer']
    if wrong:assert row['state']=='failed' and not state.get('answer')
    else:
        assert row['state']=='awaiting_review'
        assert row['result']['review_warnings'][0]['items']==state['plan']['todo']
        assert row['result']['completion_contract']=='evidence-v2-plans-advisory'
