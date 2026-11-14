from copy import deepcopy
import json
from pathlib import Path
import pytest

from app.evaluation.dossier_cases import build
from app.evaluation.dossier import execute, score, local_connect, FixtureClient
from app.investigation.dossier import selected, BoundSource, verify
from app.investigation.harness import InvestigationService, HarnessWorker
from app.service.store import Conflict

ROOT = Path(__file__).resolve().parents[1]


def test_policy_time_scope_and_identical_fact_counterfactual():
    cases = build()
    for case in cases:
        docs = selected(case['snapshot'])
        assert 'policy-current' in docs
        assert not {'policy-old','policy-future','cross-scope','future-refund'} & set(docs)
        assert not any(key in case['snapshot'] for key in ('gold','verdict','reconciliation','history_complete'))
    for a, b in ((cases[0], cases[1]), (cases[6], cases[7])):
        left = [d['body'] for d in a['snapshot']['documents'] if d['kind']!='policy']
        right = [d['body'] for d in b['snapshot']['documents'] if d['kind']!='policy']
        assert a['question']==b['question'] and left==right and a['gold']['values']==b['gold']['values']
        assert a['gold']['verdict']!=b['gold']['verdict']


@pytest.mark.parametrize('strategy',['all_context','adaptive'])
@pytest.mark.parametrize('index',[0,2,3,4,5,6])
def test_raw_contract_integration(tmp_path, strategy, index):
    case = build()[index]
    record = execute(case, strategy, tmp_path/'run')
    assert record['score']['task_passed'], record
    assert record['model_requests']==0 and record['policy_queries']==1
    assert record['client_kind']=='gold_assisted_contract_fixture_NOT_PERFORMANCE'
    result = json.loads((tmp_path/'run/result.json').read_text(encoding='utf-8'))
    assert result['validation']['semantic_validation'] is False
    assert result['validation']['human_review_required'] is True
    requests = [json.loads(p.read_text(encoding='utf-8')) for p in (tmp_path/'run').glob('request-*.json')]
    assert all('required_lines' not in json.dumps(r) and '"gold"' not in json.dumps(r) for r in requests)
    if strategy=='all_context':
        assert len(requests)==1 and requests[0]['tools']==[]
        assert record['originals_read']==8


def test_citation_checks_do_not_claim_semantic_truth(tmp_path):
    case = build()[0]
    execute(case,'all_context',tmp_path/'run')
    result = json.loads((tmp_path/'run/result.json').read_text(encoding='utf-8'))
    answer, evidence = result['answer'],result['evidence']
    bad = deepcopy(answer);bad['claims'][0]['value']=99
    assert verify(bad,evidence)['semantic_validation'] is False
    assert not score(bad,evidence,case['gold'],finished=True)['task_passed']
    bad = deepcopy(answer);bad['claims'][0]['citations'][0]['line_end']=900
    with pytest.raises(ValueError):verify(bad,evidence)
    bad = deepcopy(answer);bad['claims'][0]['citations'][0]['evidence_id']='E999'
    with pytest.raises(KeyError):verify(bad,evidence)
    bad = deepcopy(answer);bad['claims'][0]['value']=True
    with pytest.raises(Exception):verify(bad,evidence)


def test_incomplete_coverage_cannot_be_declared_closed(tmp_path):
    case=build()[3];execute(case,'all_context',tmp_path/'run')
    result=json.loads((tmp_path/'run/result.json').read_text(encoding='utf-8'))
    bad=deepcopy(result['answer']);bad['verdict']='close_explained';bad['missing']=[]
    with pytest.raises(ValueError):verify(bad,result['evidence'])


def test_no_cross_scope_read_or_future_policy():
    source=BoundSource(build()[0]['snapshot'])
    with pytest.raises(KeyError):source.call('case_read',{'document_id':'future-refund'})
    with pytest.raises(ValueError):source.call('case_read',{'document_id':'policy-current'})
    value=source.call('policy_search',{'query':'close every dispute'})
    assert [d['id'] for d in value['data']['documents']]==['policy-current']


def test_strategy_is_host_owned_and_idempotent(tmp_path):
    case=build()[0]
    service=InvestigationService(tmp_path,ROOT/'dossier_skills',strategy='all_context')
    service.submit('same-key',case['snapshot'],case['question'])
    changed=InvestigationService(tmp_path,ROOT/'dossier_skills',strategy='adaptive')
    with pytest.raises(Conflict):changed.submit('same-key',case['snapshot'],case['question'])


def test_frozen_input_change_blocks_before_client(tmp_path):
    case=build()[0];service=InvestigationService(tmp_path,ROOT/'dossier_skills')
    identity,_=service.submit('bound-input',case['snapshot'],case['question'])
    with service.store.transaction() as db:
        row=db.execute('SELECT spec FROM jobs WHERE id=?',(identity,)).fetchone()
        spec=json.loads(row['spec']);spec['snapshot']['scope']='other-channel'
        db.execute('UPDATE jobs SET spec=? WHERE id=?',(json.dumps(spec),identity))
    class NeverCalled:
        simulation=True
        def chat(self,*args,**kwargs):raise AssertionError('Network/client forbidden')
    row=HarnessWorker(service,client_factory=NeverCalled,connection_factory=local_connect).step()
    assert row['state']=='blocked'


def test_raw_claims_need_explicit_content_bound_review(tmp_path):
    from app.investigation.harness_store import HarnessStore
    from app.investigation.tickets import Tickets,MockDestination
    execute(build()[0],'all_context',tmp_path/'run')
    result=json.loads((tmp_path/'run/result.json').read_text(encoding='utf-8'))
    store=HarnessStore(tmp_path/'run/harness/jobs.sqlite')
    tickets=Tickets(store,MockDestination(tmp_path/'destination.sqlite'))
    draft=tickets.draft(result['job'],'Please review the pending duplicate payment.')
    with pytest.raises(Conflict,match='explicit_semantic_review_required'):
        tickets.approve(result['job'],draft['hash'])
    reviewed=['settled_debits','net_debit_cents','reversal_state','coverage','policy','missing']
    tickets.approve(result['job'],draft['hash'],reviewed_claims=reviewed)
    changed=tickets.draft(result['job'],'Changed action for analyst.',expected_hash=draft['hash'])
    with pytest.raises(Conflict):tickets.submit(result['job'],changed['hash'])
    assert store.events(result['job'])[-2]['kind']=='ticket_approved'


@pytest.mark.parametrize('fault',['citation','policy','repeat_bad','coverage','ledger_span'])
def test_public_draft_feedback_preserves_original_and_is_bounded(tmp_path,fault):
    from app.investigation.dossier_service import DossierService
    case=build()[3 if fault=='coverage' else 2];service=DossierService(tmp_path)
    job,_=service.submit('draft-review',case['snapshot'],case['question'])
    class Repair(FixtureClient):
        calls=0
        def chat(self,messages,tools=None):
            self.calls+=1
            response=super().chat(messages,tools)
            frame=json.loads(messages[1]['content'])
            if self.calls==1 or fault=='repeat_bad':
                answer=json.loads(response['content'])
                if fault=='policy':answer['verdict']='escalate_duplicate'
                elif fault=='coverage':answer['verdict']='await_reversal';answer['missing']=[]
                elif fault=='ledger_span':answer['claims'][0]['citations'][0].update(line_start=3,line_end=3)
                else:answer['claims'][0]['citations']=answer['claims'][0]['citations'][:1]
                response['content']=json.dumps(answer)
            else:
                assert tools==[]
                assert 'gold' not in json.dumps(frame['draft_review'])
                assert frame['draft_review']['issues']
            return response
    client=Repair(case['gold'])
    row=HarnessWorker(service,client_factory=lambda:client,connection_factory=local_connect).step()
    state=service.store.load(job)
    assert client.calls==2
    assert state['draft_reviews'][0]['answer']!=state.get('answer')
    assert len(state['draft_reviews'])==(2 if fault=='repeat_bad' else 1)
    assert row['state']==('failed' if fault=='repeat_bad' else 'awaiting_review')


def test_budget_exhaustion_leaves_a_final_answer_turn(tmp_path):
    case=build()[0];service=InvestigationService(tmp_path,ROOT/'dossier_skills')
    job,_=service.submit('budget-final',case['snapshot'],case['question'])
    class LastAnswer(FixtureClient):
        calls=0
        def chat(self,messages,tools=None):
            self.calls+=1
            if self.calls==1:
                calls=[('load_skill',{'name':'raw-payment-investigation'}),('policy_search',{'query':'dispute'})]
                calls += [('case_read',{'document_id':n}) for n in ['clearing','mapping','refund-ledger','refund-ops','intake','merchant-mail','shipping','fee-note']]
                calls += [('case_search',{'query':q}) for q in ['payment','refund']]
                return dict(role='assistant',content=None,tool_calls=[dict(id=str(i),type='function',function=dict(name=n,arguments=json.dumps(a))) for i,(n,a) in enumerate(calls)])
            assert tools==[]
            return super().chat(messages,tools)
    client=LastAnswer(case['gold'])
    row=HarnessWorker(service,client_factory=lambda:client,connection_factory=local_connect).step()
    assert row['state']=='awaiting_review' and client.calls==2
    state=service.store.load(job)
    assert state['attempts']==12 and state['force_final']


def test_source_role_filter_and_default_small_case_strategy(tmp_path):
    from app.investigation.dossier_service import DossierService
    case=build()[0]
    hits=BoundSource(case['snapshot']).call('case_search',{'query':'ORD','role':'identity'})['data']['hits']
    assert len(hits)==1 and hits[0]['id']=='mapping'
    service=DossierService(tmp_path);job,_=service.submit('default-simple',case['snapshot'],case['question'])
    assert service.store.get(job)['spec']['strategy']=='all_context'


def test_last_request_is_reserved_for_final_review(tmp_path):
    case=build()[0];service=InvestigationService(tmp_path,ROOT/'dossier_skills')
    job,_=service.submit('reserve-last',case['snapshot'],case['question'])
    class Slow(FixtureClient):
        calls=0
        def chat(self,messages,tools=None):
            self.calls+=1
            if self.calls==6:
                assert tools==[]
                return super().chat(messages,tools)
            calls=([('load_skill',{'name':'raw-payment-investigation'}),('policy_search',{'query':'dispute'})]
                   if self.calls==1 else [('case_read',{'document_id':['mapping','clearing','refund-ops','refund-ledger'][self.calls-2]})])
            return dict(role='assistant',content=None,tool_calls=[dict(id=str(i),type='function',function=dict(name=n,arguments=json.dumps(a))) for i,(n,a) in enumerate(calls)])
    row=HarnessWorker(service,client_factory=lambda:Slow(case['gold']),connection_factory=local_connect).step()
    assert row['state']=='awaiting_review'
    state=service.store.load(job)
    assert state['turn']==6 and state['force_final'] and state['attempts']==6
