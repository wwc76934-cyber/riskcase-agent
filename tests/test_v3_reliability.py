from copy import deepcopy
import json
import sqlite3
import pytest
from tests.test_fraud_inquiry import inquiry_snapshot
from app.evaluation.fraud_world_data import make_case
from app.investigation.fraud_world import World
from app.investigation.contracts import digest
from app.investigation.harness_store import HarnessStore
from app.service.store import Blocked,LeaseLost
from app.investigation.v3.output import AcquiredEvidence,OutputFailure,extract,assemble
from app.investigation.v3.control import ImmutableReads,next_stage,journaled_response
from app.investigation.v3.delivery import StagedDelivery


@pytest.fixture
def acquired(inquiry_snapshot):
    runtime,_=make_case(inquiry_snapshot,'auth_denial',1,'v3-unit-fixture')
    world=World(runtime);evidence={}
    for i,name in enumerate(['inquiry_overview','world_materials'],1):
        result=world.call(name,{});evidence['E'+str(i)]=dict(result=result,sha256=digest(result))
    a=runtime['transaction_snapshot']
    ledger=AcquiredEvidence(case_id=runtime['case_id'],cutoff=runtime['cutoff'],as_of=runtime['investigation_as_of'],
        subject_id=a['rows'][-1]['user_id'],transaction_ids=[r['transaction_id'] for r in a['rows']]).ingest(evidence)
    ref=next(k for k,v in ledger.lines.items() if v['source_type']=='authentication' and 'result=success' in v['text'])
    facts=dict(facts=[dict(id='F1',predicate='authentication',value='success',transaction_ids=[a['target_id']],refs=[ref])])
    decision=dict(fact_ids=['F1'],hypotheses=[dict(description='Authorization still unresolved',status='open',support=['F1'],counterevidence=[],unknowns=['Customer authority'])],
        tensions=[],timeline_refs=[next(k for k,v in ledger.events.items() if v['source_id']==a['target_id'])],
        recommendations=[dict(action='analyst_review',fact_ids=['F1'],reason='Human verification required')],
        missing=['confirmed_case_outcome'],stop_reason='sufficient_for_human_review',summary='Authentication is a source statement, not confirmed authorization.')
    return ledger,evidence,facts,decision


def test_two_stages_assemble_only_selected_read_events_and_source_lines(acquired):
    ledger,_,facts,decision=acquired
    extraction=extract(facts,ledger);report=assemble(decision,extraction,ledger)
    assert report['human_review_required'] and not report['semantic_truth_validated']
    assert len(report['timeline'])==1 < len(ledger.events)
    assert report['facts'][0]['citations'][0]['text']==ledger.lines[facts['facts'][0]['refs'][0]]['text']
    assert 'event_time' in report['timeline'][0] and 'status' not in decision


@pytest.mark.parametrize('change,code',[
    ('unknown','unacquired_reference'),('role','source_role'),('duplicate','duplicate_fact_id'),('extra','model_schema')])
def test_extraction_rejects_unread_wrong_role_duplicate_and_extra_fields(acquired,change,code):
    ledger,_,facts,_=acquired;facts=deepcopy(facts)
    if change=='unknown':facts['facts'][0]['refs']=['never-read']
    elif change=='role':facts['facts'][0]['refs']=[next(k for k,v in ledger.lines.items() if v['source_type']=='customer')]
    elif change=='duplicate':facts['facts']*=2
    else:facts['facts'][0]['source_id']='model-cannot-supply-provenance'
    with pytest.raises(OutputFailure,match=code):extract(facts,ledger)


@pytest.mark.parametrize('change,code',[
    ('unknown_fact','unknown_fact'),('unread_event','unacquired_event'),('money','model_schema'),
    ('outcome','outcome_not_confirmed'),('unsupported_hypothesis','hypothesis_status_without_reference')])
def test_report_does_not_relax_business_scope_or_fill_unread_evidence(acquired,change,code):
    ledger,_,facts,decision=acquired;decision=deepcopy(decision)
    if change=='unknown_fact':decision['fact_ids']=['F999']
    elif change=='unread_event':decision['timeline_refs']=['never-read-event']
    elif change=='money':decision['recommendations'][0]['action']='refund'
    elif change=='outcome':decision['missing']=['customer_statement']
    else:decision['hypotheses'][0].update(status='supported',support=[])
    with pytest.raises(OutputFailure,match=code):assemble(decision,extract(facts,ledger),ledger)


def test_cross_case_or_changed_extraction_cannot_be_reused(acquired):
    ledger,_,facts,decision=acquired;e=extract(facts,ledger)
    changed=deepcopy(e);changed['facts']['F1']['value']='failure'
    with pytest.raises(OutputFailure,match='extraction_changed'):assemble(decision,changed,ledger)
    ledger.case_id='another-case'
    with pytest.raises(OutputFailure,match='extraction_scope_changed'):assemble(decision,e,ledger)


@pytest.mark.parametrize('change,code',[('future','future_original'),('subject','original_subject'),('hash','source_scope_or_hash')])
def test_ingestion_fences_scope_and_future_material(acquired,change,code):
    ledger,evidence,_,_=acquired;evidence=deepcopy(evidence)
    if change=='future':evidence['E2']['result']['data']['B'][0]['available_at']='2030-01-01T00:00:00'
    elif change=='subject':evidence['E2']['result']['data']['B'][0]['subject_id']='foreign-user'
    else:evidence['E2']['result']['case_id']='foreign-case'
    evidence['E2']['sha256']=digest(evidence['E2']['result'])
    with pytest.raises(OutputFailure,match=code):ledger.ingest(evidence)


def test_immutable_reads_deduplicate_but_do_not_hide_new_versions_or_tool_failures():
    cache=ImmutableReads();calls=[];one={'text':'first'};two={'text':'corrected'}
    def fetch(value):calls.append(1);return value
    a=cache.read('source',1,digest(one),lambda:fetch(one));a['text']='caller-mutated'
    assert cache.read('source',1,digest(one),lambda:fetch(one))==one and len(calls)==1
    with pytest.raises(OutputFailure,match='immutable_version_hash_changed'):cache.read('source',1,digest(two),lambda:fetch(two))
    assert cache.read('source',2,digest(two),lambda:fetch(two))==two and len(calls)==2
    def missing():raise FileNotFoundError()
    def down():raise TimeoutError()
    with pytest.raises(OutputFailure,match='source_missing'):cache.read('missing',1,digest(one),missing)
    with pytest.raises(OutputFailure,match='tool_unavailable'):cache.read('down',1,digest(one),down)


def test_request_policy_reserves_extraction_and_report_before_exhaustion():
    assert [next_stage(used_requests=i,max_requests=6) for i in range(7)]==['investigate']*4+['extract','budget_exhausted','budget_exhausted']
    assert next_stage(used_requests=5,max_requests=6,extraction_complete=True)=='report'
    assert next_stage(used_requests=6,max_requests=6,extraction_complete=True)=='budget_exhausted'


class Client:
    simulation=True
    def __init__(self,payloads):self.payloads=list(payloads);self.calls=0
    def chat(self,messages,tools=None):
        self.calls+=1
        value=self.payloads.pop(0)
        if isinstance(value,BaseException):raise value
        return dict(role='assistant',content=json.dumps(value))
    def snapshot(self):return dict(simulated=True,requests=self.calls,estimated_cny='0')


def job(tmp_path,clock=lambda:100):
    store=HarnessStore(tmp_path/'jobs.sqlite',clock=clock)
    identity,_=store.submit('v3',dict(mode='offline',case_id='v3-unit-fixture',question=dict(text='Offline fixture')))
    claim=store.claim('first',lease_seconds=30,deadline_seconds=180,mode='offline')
    return store,identity,claim


@pytest.mark.parametrize('crash_point',['response_saved','raw_checkpoint_saved'])
def test_interruption_replays_or_parses_saved_response_without_duplicate_call(tmp_path,acquired,crash_point):
    ledger,_,facts,decision=acquired;now=[100];store,identity,claim=job(tmp_path,lambda:now[0]);client=Client([facts])
    class Crash(BaseException):pass
    def hook(kind,ordinal):
        if kind==crash_point and ordinal==1:raise Crash()
    with pytest.raises(Crash):StagedDelivery(store,claim,ledger,client,max_requests=2,hook=hook).run()
    now[0]=131;replacement=store.claim('second',lease_seconds=30,deadline_seconds=180,mode='offline')
    assert replacement['generation']==2
    resumed=Client([decision]);report=StagedDelivery(store,replacement,ledger,resumed,max_requests=2).run()
    assert report['status']=='awaiting_analyst_review' and client.calls==resumed.calls==1
    with pytest.raises(LeaseLost):store.checkpoint(identity,claim['owner'],claim['generation'])
    assert len(store.load(identity)['raw_responses'])==2
    assert StagedDelivery(store,replacement,ledger,Client([]),max_requests=2).run()==report


def test_invalid_response_is_retained_and_never_silently_retried(tmp_path,acquired):
    ledger,_,_,_=acquired;store,identity,claim=job(tmp_path);client=Client([dict(garbage='retained')])
    with pytest.raises(OutputFailure,match='model_schema'):StagedDelivery(store,claim,ledger,client).run()
    with pytest.raises(OutputFailure,match='terminal_delivery_failure'):StagedDelivery(store,claim,ledger,client).run()
    assert client.calls==1 and store.load(identity)['raw_responses'][0]['content']=='{"garbage": "retained"}'
    with sqlite3.connect(store.path) as db:assert json.loads(db.execute('SELECT audit FROM calls').fetchone()[0])['requests']==1


def test_unknown_transport_blocks_reissue_with_original_audit(tmp_path):
    store,_,claim=job(tmp_path);calls=[]
    def uncertain():calls.append(1);raise TimeoutError('simulated unknown charge')
    with pytest.raises(TimeoutError):journaled_response(store,claim,1,{'stage':'extract'},uncertain,audit_snapshot=lambda:dict(unknown_cost=True,reserved_estimate_cny='.05'))
    with pytest.raises(Blocked,match='unresolved_call'):journaled_response(store,claim,1,{'stage':'extract'},uncertain)
    assert len(calls)==1


def test_saved_null_response_is_replayed_instead_of_resent(tmp_path):
    store,_,claim=job(tmp_path);calls=[]
    def invoke():calls.append(1);return None
    assert journaled_response(store,claim,1,{'stage':'extract'},invoke)==(None,False)
    assert journaled_response(store,claim,1,{'stage':'extract'},invoke)==(None,True)
    assert len(calls)==1


def test_late_response_and_cost_snapshot_survive_expired_lease(tmp_path,acquired):
    ledger,_,facts,_=acquired;now=[100];store,identity,claim=job(tmp_path,lambda:now[0])
    class LateClient(Client):
        def chat(self,messages,tools=None):
            response=super().chat(messages,tools);now[0]=131;return response
    client=LateClient([facts])
    with pytest.raises(LeaseLost):StagedDelivery(store,claim,ledger,client).run()
    directory=tmp_path/'v3-model-records'/identity
    assert json.loads((directory/'response-01.json').read_text())['content']
    assert json.loads((directory/'usage-01.json').read_text())['requests']==1
    assert store.claim('replacement',mode='offline') is None
    assert store.get(identity)['state']=='blocked' and client.calls==1


def legacy_answer(evidence):
    from app.investigation.world_baseline import report_from_observations
    answer=report_from_observations([dict(ok=True,evidence_id=k,**v['result']) for k,v in evidence.items()])
    row=evidence['E2']['result']['data']['A']['rows'][0]
    answer['timeline'].append(dict(source_id=row['transaction_id'],evidence_id='E1'))
    return answer


def test_offline_rebinding_preserves_facts_decisions_and_original_input(acquired):
    from app.investigation.fraud_world import materialize_timeline,DraftError,verify
    from app.investigation.v3.offline_replay import rebind_saved_timeline
    _,evidence,_,_=acquired;answer=legacy_answer(evidence);before=digest(answer)
    with pytest.raises(DraftError):materialize_timeline(answer,evidence)
    fixed,changes=rebind_saved_timeline(answer,evidence);verify(fixed,evidence)
    assert digest(answer)==before
    assert fixed['observations']==answer['observations'] and fixed['recommendations']==answer['recommendations']
    assert any(c['operation']=='rebind_acquired_original_container' for c in changes)
    assert {e['source_id'] for e in fixed['timeline']}=={e['source_id'] for e in answer['timeline']}


def test_offline_rebinding_cannot_acquire_an_unread_or_ambiguous_original(acquired):
    from app.investigation.v3.offline_replay import rebind_saved_timeline
    _,evidence,_,_=acquired;answer=legacy_answer(evidence)
    answer['timeline']=[answer['timeline'][-1]]
    with pytest.raises(ValueError,match='never acquired'):rebind_saved_timeline(answer,{'E1':evidence['E1']})
    ambiguous=deepcopy(evidence);ambiguous['E3']=deepcopy(evidence['E2'])
    ambiguous['E3']['result']['data']['A']['rows'][0]['amount_cents']+=1
    ambiguous['E3']['sha256']=digest(ambiguous['E3']['result'])
    with pytest.raises(ValueError,match='Ambiguous'):rebind_saved_timeline(answer,ambiguous)


def test_two_stage_delivery_consumes_real_mcp_acquisitions(tmp_path,inquiry_snapshot):
    import asyncio
    from app.investigation.gateway import connect,Gateway
    runtime,_=make_case(inquiry_snapshot,'auth_denial',1,'v3-mcp-fixture')
    path=tmp_path/'snapshot.json';path.write_text(json.dumps(runtime),encoding='utf-8')
    async def acquire():
        with (tmp_path/'mcp.log').open('w',encoding='utf-8') as log:
            async with connect(path,runtime,errlog=log) as (sessions,handshakes):
                gateway=Gateway(sessions,runtime)
                await gateway.call('world_materials','{}')
                return gateway.evidence,handshakes
    evidence,handshakes=asyncio.run(acquire())
    a=runtime['transaction_snapshot'];ledger=AcquiredEvidence(case_id=runtime['case_id'],cutoff=runtime['cutoff'],
        as_of=runtime['investigation_as_of'],subject_id=a['rows'][-1]['user_id'],transaction_ids=[r['transaction_id'] for r in a['rows']]).ingest(evidence)
    assert set(handshakes)=={'transactions','documents'} and ledger.lines
    # Use source-bound fixture responses; do not pretend this client reasons.
    ref=next(k for k,v in ledger.lines.items() if v['source_type']=='authentication')
    facts=dict(facts=[dict(id='F1',predicate='authentication',value='success',transaction_ids=[a['target_id']],refs=[ref])])
    decision=dict(fact_ids=['F1'],hypotheses=[dict(description='Source-bound probe',status='open',support=['F1'],counterevidence=[],unknowns=['Outcome'])],
        tensions=[],timeline_refs=[],recommendations=[dict(action='analyst_review',fact_ids=['F1'],reason='Review')],missing=['confirmed_case_outcome'],stop_reason='unavailable_evidence',summary='MCP probe')
    store,_,claim=job(tmp_path/'delivery');report=StagedDelivery(store,claim,ledger,Client([facts,decision])).run()
    assert report['facts'][0]['citations'][0]['source_id']==ledger.lines[ref]['source_id']
