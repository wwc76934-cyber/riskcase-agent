import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor

import pytest
from app.investigation.harness import InvestigationService,HarnessWorker
from app.investigation.skills import freeze,catalog,read
from app.investigation.fixtures import development_cases
from app.investigation.context import messages
from app.investigation.tickets import Tickets,MockDestination
from app.service.store import Conflict,Blocked,LeaseLost

ROOT=Path(__file__).resolve().parents[1]


@pytest.fixture
def setup(tmp_path):
    service=InvestigationService(tmp_path,ROOT/'investigation_skills')
    case=development_cases()[2]
    job,_=service.submit('stage2-request',case['snapshot'],'Investigate merchant change')
    return service,job


def test_progressive_disclosure_and_resource_boundary():
    bundle=freeze(ROOT/'investigation_skills')
    assert len(bundle)==3 and all('body' not in m for m in catalog(bundle))
    loaded=read(bundle,'alert-investigation')
    assert not loaded['grants_permissions'] and 'references/precedence.md' in loaded['text']
    assert 'one settled debit' in read(bundle,'alert-investigation','references/precedence.md')['text']
    with pytest.raises(ValueError):read(bundle,'alert-investigation','../../secret')
    with pytest.raises(ValueError):read(bundle,'no-such-skill')
    bundle['alert-investigation']['files']['SKILL.md']+='changed'
    with pytest.raises(ValueError):read(bundle,'alert-investigation')


def test_workflow_and_content_bound_ticket(setup):
    s,job=setup;row=HarnessWorker(s).step()
    assert row['state']=='awaiting_review',row
    state=s.store.load(job)
    assert state['phase']=='done' and not state['plan']['todo']
    assert list(state['skills'])==['merchant-change']
    assert row['result']['turns']<=6
    tickets=Tickets(s.store,MockDestination(s.directory/'destination.sqlite'))
    draft=tickets.draft(job,'Analyst review required')
    with pytest.raises(Conflict):tickets.submit(job,draft['hash'])
    tickets.approve(job,draft['hash'])
    edited=tickets.draft(job,'Changed analyst action',expected_hash=draft['hash'])
    with pytest.raises(Conflict):tickets.submit(job,edited['hash'])
    with pytest.raises(Conflict):tickets.approve(job,draft['hash'])
    tickets.approve(job,edited['hash'])
    def response_lost():raise ConnectionError('Response lost after destination commit')
    with pytest.raises(ConnectionError):tickets.submit(job,edited['hash'],after_created=response_lost)
    assert tickets.get(job)['status']=='uncertain'
    with pytest.raises(Conflict):tickets.draft(job,'Change while uncertain',expected_hash=edited['hash'])
    with ThreadPoolExecutor(2) as pool: receipts=list(pool.map(lambda _:tickets.submit(job,edited['hash']),range(2)))
    assert receipts[0]['receipt']['id']==receipts[1]['receipt']['id']
    with sqlite3.connect(s.directory/'destination.sqlite') as db:assert db.execute('SELECT count(*) FROM tickets').fetchone()[0]==1


@pytest.mark.parametrize('point',['response_saved','tool_returned','tool_checkpoint'])
def test_real_process_crash_and_resume(tmp_path,point):
    env=dict(os.environ,PYTHONPATH=os.pathsep.join(p for p in sys.path if p))
    base=[sys.executable,'-X','utf8','-B','-m','app.cli.harness_demo']
    def invoke(*args):return subprocess.run(base+list(args)+['--directory',str(tmp_path)],cwd=ROOT,env=env,capture_output=True,text=True,timeout=45,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    assert invoke('init').returncode==0
    crash=invoke('worker','--crash',point,'--ordinal','2' if point=='response_saved' else '3')
    assert crash.returncode==23,crash.stderr
    # Advance the expired lease condition without a 90-second wall-clock sleep.
    with sqlite3.connect(tmp_path/'jobs.sqlite') as db:db.execute('UPDATE jobs SET lease_until=0 WHERE state="running"')
    recovered=invoke('worker')
    assert recovered.returncode==0,recovered.stderr
    s=InvestigationService(tmp_path,ROOT/'investigation_skills');job=json.loads((tmp_path/'job.json').read_text())['id']
    row=s.store.get(job);assert row['state']=='awaiting_review',row
    assert row['generation']==2
    if point=='response_saved':assert row['result']['replayed_client_responses']==1
    if point=='tool_returned':assert row['result']['attempts']==12  # Includes the frozen knowledge lookup.
    assert s.store.load(job)['skills']['merchant-change']['version']==row['spec']['skills']['merchant-change']['version']


def test_uncertain_model_request_is_not_retried(setup):
    s,job=setup
    claim=s.store.claim('dead')
    s.store.begin_call(job,'dead',claim['generation'],1,'request')
    with s.store.transaction() as db:db.execute('UPDATE jobs SET lease_until=0 WHERE id=?',(job,))
    assert HarnessWorker(s).step() is None
    assert s.store.get(job)['reason']=='unresolved_call'


def test_fenced_checkpoint_and_corruption(setup):
    s,job=setup;claim=s.store.claim('old')
    state=dict(turn=0,attempts=0,phase='model')
    s.store.save(claim,state)
    with s.store.transaction() as db:db.execute('UPDATE jobs SET lease_until=0 WHERE id=?',(job,))
    s.store.claim('new')
    with pytest.raises(LeaseLost):s.store.save(claim,state)
    with s.store.transaction() as db:db.execute("UPDATE investigation_state SET body='{}' WHERE job_id=?",(job,))
    with pytest.raises(Blocked):s.store.load(job)


def test_compaction_preserves_facts_gaps_and_original_access(setup):
    s,job=setup;HarnessWorker(s).step();state=s.store.load(job);spec=s.store.get(job)['spec']
    original=deepcopy(state)
    context,metric=messages(spec,state)
    restored=json.loads(json.dumps(state,sort_keys=True))
    assert messages(spec,restored)==messages(spec,state)
    assert metric['omitted_prose']
    frame=json.loads(context[1]['content'])
    assert len(frame['observations'])==len(state['observations'])
    assert frame['unknowns']==state['plan']['unknowns']
    assert state==original
    key=metric['omitted_prose'][0];state['expanded']=[key]
    expanded,_=messages(spec,state)
    observation=next(o for o in json.loads(expanded[1]['content'])['observations'] if o.get('evidence_id')==key)
    assert observation['data']['body']==state['evidence'][key]['result']['data']['body']
    with pytest.raises(Blocked):messages(spec,state,limit=100)


def test_skill_versions_are_frozen_for_queued_tasks(tmp_path):
    import shutil
    skill_root=tmp_path/'skills';shutil.copytree(ROOT/'investigation_skills',skill_root)
    s=InvestigationService(tmp_path/'service',skill_root);case=development_cases()[0]
    job,_=s.submit('queued-old-skill',case['snapshot'],'Investigate alerts')
    before=s.store.get(job)['spec']['skills']['alert-investigation']['version']
    path=skill_root/'alert-investigation/SKILL.md';path.write_text(path.read_text()+'\nUpdated guidance.',encoding='utf-8')
    newer,_=s.submit('queued-new-skill',case['snapshot'],'Investigate alerts')
    assert s.store.get(newer)['spec']['skills']['alert-investigation']['version']!=before
    HarnessWorker(s).step()
    assert s.store.load(job)['skills']['alert-investigation']['version']==before


def test_unknown_skill_cannot_enable_tools(setup):
    class Bad:
        simulation=True
        def chat(self,*args,**kwargs):
            return dict(role='assistant',content=None,tool_calls=[dict(id='x',type='function',function=dict(name='load_skill',arguments='{"name":"shell-access"}'))])
    s,job=setup;row=HarnessWorker(s,client_factory=Bad).step()
    assert row['state']=='blocked' and row['reason']=='no_progress_repeated_call'
    state=s.store.load(job);assert not state['evidence'] and not state['skills']


def test_authenticated_workbench_routes(tmp_path):
    from fastapi.testclient import TestClient
    from app.service.runtime import ResearchService,Settings,Worker
    from app.service.api import create_app
    parent=ResearchService(Settings(ROOT,tmp_path,{},'t'*40))
    with TestClient(create_app(parent)) as api:
        assert api.get('/investigations').status_code==401
        assert api.get('/investigations/page').status_code==200
        api.headers.update({'Authorization':'Bearer '+'t'*40,'Idempotency-Key':'harness-browser-test'})
        request=dict(case_id='case-04',text='Investigate evidence conflict')
        first=api.post('/investigations',json=request);assert first.status_code==200,first.text
        job=first.json()['id']
        assert api.post('/investigations',json=request).json()['id']==job
        assert api.post('/investigations',json=request|{'mode':'live'}).status_code==422
        row=Worker(parent).step();assert row['state']=='awaiting_review'
        detail=api.get('/investigations/'+job).json()
        assert list(detail['checkpoint']['skills'])==['evidence-conflict']
        draft=api.post('/investigations/'+job+'/draft',json={'text':'请人工核对证据冲突'}).json()
        assert api.post('/investigations/'+job+'/submit-ticket',json={'expected_hash':draft['hash']}).status_code==409
        assert api.post('/investigations/'+job+'/approve',json={'expected_hash':draft['hash']}).status_code==200
        a=api.post('/investigations/'+job+'/submit-ticket',json={'expected_hash':draft['hash']}).json()
        b=api.post('/investigations/'+job+'/submit-ticket',json={'expected_hash':draft['hash']}).json()
        assert a['receipt']['id']==b['receipt']['id']


def test_reference_loading_and_skill_cannot_grant_shell(setup):
    from app.investigation.harness import SkillScriptedClient
    class ReferenceClient(SkillScriptedClient):
        def chat(self,messages,tools=None):
            response=super().chat(messages,tools)
            if response.get('tool_calls') and response['tool_calls'][0]['function']['name']=='load_skill':
                # Spend the finite tool budget on resource/permission probes in this test.
                response['tool_calls']=[c for c in response['tool_calls'] if c['function']['name']!='search_knowledge']
                response['tool_calls'][0]['function']['arguments']='{"name":"alert-investigation"}'
                response['tool_calls'].insert(1,dict(id='ref',type='function',function=dict(name='read_skill_resource',arguments='{"name":"alert-investigation","resource":"references/precedence.md"}')))
                response['tool_calls'].insert(2,dict(id='not-granted',type='function',function=dict(name='shell',arguments='{}')))
            return response
    s,job=setup;row=HarnessWorker(s,client_factory=ReferenceClient).step()
    assert row['state']=='awaiting_review',row
    state=s.store.load(job)
    assert 'alert-investigation/references/precedence.md' in state['skills']
    assert state['trace'][0]['tool']=='shell' and state['trace'][0]['error']=='permission_denied'
    assert state['attempts']==12


def test_incomplete_history_remains_unknown_after_resume_context(tmp_path):
    s=InvestigationService(tmp_path,ROOT/'investigation_skills');case=development_cases()[5]
    job,_=s.submit('incomplete-history',case['snapshot'],'Investigate alerts')
    row=HarnessWorker(s).step();assert row['state']=='awaiting_review'
    state=s.store.load(job);frame=json.loads(messages(s.store.get(job)['spec'],state)[0][1]['content'])
    assert frame['unknowns'] and row['result']['answer']['verdict']=='insufficient_evidence'
    assert any(o.get('source')=='transactions' and o['data']['history_complete'] is False for o in frame['observations'])
