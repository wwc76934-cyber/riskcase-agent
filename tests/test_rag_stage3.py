from copy import deepcopy
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import json
import pytest
from app.investigation.knowledge import corpus,document,SCOPE,AS_OF
from app.investigation.retrieval import search,eligible
from app.investigation.experience import evaluate,search_memory
from app.investigation.harness import InvestigationService,HarnessWorker
from app.investigation.fixtures import development_cases
from app.service.store import Conflict,digest

ROOT=Path(__file__).resolve().parents[1]
GOOD=['coverage','settlement','merchant']
BAD=['merchant','settlement','coverage']


def test_filter_before_embedding_and_tombstones():
    class Capture:
        identity={'model':'test_double'}
        def similarities(self,q,rows):
            self.rows=rows
            assert not any('未来草案' in s or '旧草案' in s or '无需审批' in s or '仍然 normal_change' in s for s in rows)
            return [.8]*len(rows)
        def rerank(self,q,rows):return [1.]*len(rows)
    backend=Capture()
    result=search(corpus(),'历史覆盖不足',scope=SCOPE,as_of=AS_OF,mode='hybrid_rerank',backend=backend)
    assert result['hits'] and result['advisory_only']
    assert len(backend.rows)==12
    assert all(h['citation'].startswith('D:') and 'content_hash' in h for h in result['hits'])
    assert not search(corpus(),'历史',scope='absent',as_of=AS_OF)['hits']


def test_retrieval_fail_closed_and_stable():
    docs=corpus()
    with pytest.raises(ValueError):search(docs,'x',scope=SCOPE,as_of=AS_OF,mode='hybrid')
    bad=deepcopy(docs);bad[0]['paragraphs'][0]='tampered'
    with pytest.raises(ValueError):eligible(bad,SCOPE,AS_OF)
    with pytest.raises(ValueError):eligible(docs+[document('coverage',['conflict'])],SCOPE,AS_OF)
    assert search(docs,'量子纠缠',scope=SCOPE,as_of=AS_OF)['status']=='insufficient_knowledge'
    a=search(docs,'重复授权清算',scope=SCOPE,as_of=AS_OF)
    assert a==search(list(reversed(docs)),'重复授权清算',scope=SCOPE,as_of=AS_OF)


def test_ranking_logit_is_not_probability_and_dense_scores_fail_closed():
    class Backend:
        identity={'test_double':True}
        def similarities(self,q,rows):return [.5]*len(rows)
        def rerank(self,q,rows):return [-1.]*len(rows)
    backend=Backend();docs=[document('coverage',['历史覆盖不足'])]
    args=dict(scope=SCOPE,as_of=AS_OF,mode='hybrid_rerank',backend=backend)
    assert not search(docs,'历史覆盖',**args)['hits']
    result=search(docs,'历史覆盖',policy='v2',**args)
    assert result['hits'] and result['candidate_rerank_scores'][0]['logit']==-1
    backend.similarities=lambda q,rows:[float('nan')]
    with pytest.raises(ValueError):search(docs,'历史覆盖',policy='v2',**args)


def test_method_gate_checks_decisions_not_rank():
    assert evaluate(GOOD)['passed']
    for order in (BAD,['settlement','coverage','merchant'],['coverage','merchant','settlement']):
        result=evaluate(order,GOOD)
        assert not result['passed'] and any(r['regressed'] for r in result['cases'])
    with pytest.raises(ValueError):evaluate(['shell','coverage','merchant'])


@pytest.fixture
def source(tmp_path):
    s=InvestigationService(tmp_path,ROOT/'investigation_skills');case=development_cases()[2]
    job,_=s.submit('method-source',case['snapshot'],'Investigate merchant change')
    # Store-level unit fixture; the integration test below runs the actual harness.
    claim=s.store.claim('fixture');s.store.finish(job,'fixture',claim['generation'],'awaiting_review',result={'synthetic_unit_fixture':True})
    return s,job,case


def test_revision_review_rollback_and_frozen_snapshot(source):
    s,job,case=source;m=s.experiences
    bad=m.candidate(job,BAD);bad=m.assess(bad['id'])
    with pytest.raises(Conflict):m.publish(bad['id'],bad['hash'],m.head()['hash'])
    good=m.candidate(job,GOOD,parent=bad['id'])
    with pytest.raises(Conflict):m.publish(good['id'],good['hash'],m.head()['hash'])
    good=m.assess(good['id']);head=m.publish(good['id'],good['hash'],m.head()['hash'])
    new,_=s.submit('published-task',case['snapshot'],'Investigate merchant change')
    frozen=s.store.get(new)['spec']['knowledge']['memory']
    assert frozen['release']==1 and 'source_job' not in json.dumps(frozen)
    assert not s.store.get(job)['spec']['knowledge']['memory']['lesson']
    assert search_memory(frozen,'历史覆盖')['hits'][0]['citation']=='M:release-1'
    assert not search_memory(frozen,'火星天气')['hits']
    m.rollback(0,head['hash'])
    assert m.frozen()['lesson'] is None
    assert s.submit('published-task',case['snapshot'],'Investigate merchant change')==(new,False)
    assert s.store.get(new)['spec']['knowledge']['memory']==frozen
    assert s.store.get(new)['spec']['knowledge_hash']==digest(s.store.get(new)['spec']['knowledge'])
    with pytest.raises(Conflict):s.submit('published-task',case['snapshot'],'changed')
    restored=m.rollback(1,m.head()['hash'])
    assert restored['lesson']['content_hash']==frozen['lesson']['content_hash']


def test_concurrent_publish_stale_candidate_and_expiry(source):
    s,job,_=source;m=s.experiences
    candidates=[m.assess(m.candidate(job,GOOD)['id']) for _ in range(2)];base=m.head()['hash']
    def publish(c):
        try:return m.publish(c['id'],c['hash'],base)['id']
        except Conflict:return 'conflict'
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(publish,candidates))
    assert sorted(results,key=str)==[1,'conflict']
    s.store.clock=lambda:10**12
    assert m.frozen()['lesson'] is None
    with pytest.raises(Conflict):m.rollback(1,m.head()['hash'])


def test_source_and_evaluation_tamper_rejected(source):
    s,job,_=source;m=s.experiences;c=m.assess(m.candidate(job,GOOD)['id'])
    with s.store.transaction() as db:db.execute("UPDATE method_candidates SET evaluation='{}' WHERE id=?",(c['id'],))
    with pytest.raises(Conflict):m.publish(c['id'],c['hash'],m.head()['hash'])
    m.assess(c['id'])
    with s.store.transaction() as db:db.execute("UPDATE jobs SET result='{}' WHERE id=?",(job,))
    with pytest.raises(Conflict):m.publish(c['id'],c['hash'],m.head()['hash'])


def test_real_resume_keeps_old_memory_after_publication(source):
    import os,subprocess,sys
    s,job,case=source
    env=dict(os.environ,PYTHONPATH=os.pathsep.join(p for p in sys.path if p))
    base=[sys.executable,'-X','utf8','-B','-m','app.cli.harness_demo']
    def invoke(*args):return subprocess.run(base+list(args)+['--directory',str(s.directory)],cwd=ROOT,env=env,capture_output=True,text=True,timeout=50,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    assert invoke('init').returncode==0
    old=json.loads((s.directory/'job.json').read_text())['id']
    assert invoke('worker','--crash','response_saved','--ordinal','2').returncode==23
    m=s.experiences;c=m.assess(m.candidate(job,GOOD)['id']);m.publish(c['id'],c['hash'],m.head()['hash'])
    with s.store.transaction() as db:db.execute('UPDATE jobs SET lease_until=0 WHERE id=?',(old,))
    recovered=invoke('worker');assert recovered.returncode==0,recovered.stderr
    row=s.store.get(old);assert row['state']=='awaiting_review',row
    assert row['generation']==2 and row['result']['replayed_client_responses']==1
    assert row['result']['knowledge_snapshot']['release']==0
    observation=next(o for o in s.store.load(old)['observations'] if o.get('kind')=='knowledge')
    assert not observation['memory']['hits']
    new,_=s.submit('after-publication',case['snapshot'],'Investigate merchant change')
    assert s.store.get(new)['spec']['knowledge']['memory']['release']==1


def test_harness_advisory_separation_and_authenticated_release(tmp_path):
    from fastapi.testclient import TestClient
    from app.service.runtime import ResearchService,Settings,Worker
    from app.service.api import create_app
    parent=ResearchService(Settings(ROOT,tmp_path,{},'t'*40))
    with TestClient(create_app(parent)) as api:
        assert api.get('/methods').status_code==401
        assert api.post('/methods/rollback',json={'target':0,'expected_head':'a'*64}).status_code==401
        api.headers.update({'Authorization':'Bearer '+'t'*40,'Idempotency-Key':'rag-stage3-source'})
        job=api.post('/investigations',json={'case_id':'case-03','text':'Investigate merchant change'}).json()['id']
        assert Worker(parent).step()['state']=='awaiting_review'
        candidate=api.post('/methods/candidates',json={'source_job':job,'order':GOOD}).json()
        assert api.post('/methods/candidates',json={'source_job':job,'order':GOOD,'text':'private data'}).status_code==422
        head=api.get('/methods').json()['head']
        request={'expected_hash':candidate['hash'],'expected_head':head['hash']}
        assert api.post('/methods/candidates/'+candidate['id']+'/publish',json=request).status_code==409
        assert api.post('/methods/candidates/'+candidate['id']+'/evaluate',json={}).json()['evaluation']['passed']
        assert api.post('/methods/candidates/'+candidate['id']+'/publish',json=request).status_code==200
        api.headers['Idempotency-Key']='rag-stage3-with-memory'
        new=api.post('/investigations',json={'case_id':'case-04','text':'Investigate conflict'}).json()['id']
        assert Worker(parent).step()['state']=='awaiting_review'
        row=api.get('/investigations/'+new).json();state=row['checkpoint']
        obs=next(o for o in state['observations'] if o.get('kind')=='knowledge')
        assert obs['memory']['hits'] and obs['retrieval']['hits']
        assert all(k.startswith('E') for k in state['evidence'])
        assert row['result']['answer']['verdict']=='needs_review' and row['knowledge_snapshot']['release']==1
        assert row['result']['attempts']<=12
