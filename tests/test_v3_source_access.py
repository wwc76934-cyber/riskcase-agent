from copy import deepcopy
import asyncio,json,time
import pytest
from app.evaluation.v3_tasks import case
from app.evaluation.v3_narratives import DEVELOPMENT
from app.service.store import JobStore,LeaseLost,Blocked,digest as state_digest,encoded
from app.service.v3_sources import SourceAccess
from app.investigation.contracts import digest
from app.investigation.harness_store import HarnessStore
from app.investigation.v3.output import AcquiredEvidence,OutputFailure
from app.investigation.v3.delivery import StagedDelivery
from app.investigation.v3.typed_review import TypedReviewContract
from tests.test_v3_reliability import Client


@pytest.fixture
def runtime():return case('development','channel_verification',0,DEVELOPMENT['channel_verification'][0],0)[0]


def start(tmp_path,runtime,*,clock=time.time,max_tools=20):
    path=tmp_path/'source-jobs.sqlite';store=JobStore(path,clock=clock)
    identity,_=store.submit('source-unit',dict(mode='offline',case_id=runtime['case_id'],question=dict(text='Author fixture')))
    claim=store.claim('source-owner',lease_seconds=120,deadline_seconds=600,mode='offline')
    return store,claim,SourceAccess(path,claim,runtime,clock=clock,max_tools=max_tools)


def ledger(runtime,source):
    return AcquiredEvidence(case_id=runtime['case_id'],cutoff=runtime['cutoff'],as_of=source.position()['as_of'],
        subject_id=runtime['subject_id'],transaction_ids=[r['transaction_id'] for r in runtime['transactions']],
        target_id=runtime['alert']['transaction_id']).ingest(source.evidence())


def test_current_catalog_future_denial_and_cached_source_do_not_rebind_evidence(tmp_path,runtime):
    _,claim,source=start(tmp_path,runtime);context=source.call('v3_context',{})
    ids=[s['source_id'] for s in context['catalog']['sources']]
    future=next(s['source_id'] for s in runtime['records'] if s['available_at']>runtime['stage_times'][2])
    assert future not in ids
    assert source.call('v3_read',dict(source_ids=[future]))['error']['code']=='source_not_available'
    assert source.call('v3_read',dict(source_ids=ids))['evidence_id']=='E2'
    before=source.evidence();cached=source.call('v3_read',dict(source_ids=ids))
    assert cached['frame'] is None and set(cached['cached_source_ids'])==set(ids)
    assert source.evidence()==before and len(ledger(runtime,source).lines)==2
    assert source.position()['tool_attempts']==4


def test_cost_dependency_and_original_receipts_survive_new_source_instance(tmp_path,runtime):
    _,claim,source=start(tmp_path,runtime);source.advance(1)
    route=next(r for r in runtime['records'] if r['source_type']=='account')
    paid=[r for r in runtime['records'] if r['cost_units']==8]
    source.advance(2)
    assert source.call('v3_read',dict(source_ids=[paid[0]['source_id']]))['error']['code']=='acquisition_dependency'
    assert source.call('v3_read',dict(source_ids=[route['source_id']]))['ok']
    assert source.call('v3_read',dict(source_ids=[r['source_id'] for r in paid]))['error']['code']=='source_budget'
    assert source.position()['catalog']['remaining_cost_units']==14
    assert source.call('v3_read',dict(source_ids=[paid[0]['source_id']]))['ok']
    restarted=SourceAccess(source.path,claim,runtime)
    assert restarted.position()['catalog']['remaining_cost_units']==6
    assert restarted.call('v3_read',dict(source_ids=[paid[0]['source_id']]))['frame'] is None
    assert restarted.position()['catalog']['remaining_cost_units']==6
    assert restarted.evidence()==source.evidence()


def test_scope_fencing_policy_and_deadline_are_host_constraints(tmp_path,runtime):
    now=[100];store,claim,source=start(tmp_path,runtime,clock=lambda:now[0],max_tools=3)
    changed=deepcopy(runtime);changed['case_id']='another-case'
    with pytest.raises(Blocked,match='source_case_scope'):SourceAccess(source.path,claim,changed,clock=lambda:now[0])
    now[0]=221;replacement=store.claim('new-owner',lease_seconds=120,deadline_seconds=600,mode='offline')
    with pytest.raises(LeaseLost):source.call('v3_context',{})
    with pytest.raises(Blocked,match='source_snapshot_or_policy_changed'):SourceAccess(source.path,replacement,runtime,max_tools=4,clock=lambda:now[0])
    current=SourceAccess(source.path,replacement,runtime,max_tools=3,clock=lambda:now[0])
    current.checkpoint(replacement['id'],replacement['owner'],replacement['generation'],lease_seconds=600)
    now[0]=701
    with pytest.raises(Blocked,match='task_deadline'):current.call('v3_context',{})


def test_invalid_and_cached_calls_consume_tool_allowance(tmp_path,runtime):
    _,_,source=start(tmp_path,runtime,max_tools=2)
    assert not source.call('v3_read',dict(source_ids=[]))['ok']
    assert source.call('v3_context',{})['ok']
    with pytest.raises(OutputFailure,match='source_tool_budget'):source.call('v3_context',{})


def test_evidence_container_order_is_numeric_after_ten_acquisitions(tmp_path,runtime):
    runtime=deepcopy(runtime);base=runtime['records'][0]
    runtime['records']=[dict(deepcopy(base),source_id='unit-original-'+str(i),available_at=runtime['stage_times'][0],
        event_time=runtime['cutoff'],coverage=dict(start=runtime['cutoff'],end=runtime['stage_times'][0],complete=True,note='Independent unit fixture'),
        version=1,supersedes_id=None,cost_units=0,latency_minutes=0,requires=[]) for i in range(12)]
    runtime['records_sha256']=digest(runtime['records']);_,_,source=start(tmp_path,runtime)
    for record in runtime['records']:assert source.call('v3_read',dict(source_ids=[record['source_id']]))['ok']
    assert list(source.evidence())==['E'+str(i) for i in range(1,14)]


def test_recomputed_hash_cannot_make_foreign_evidence_match_the_acquired_snapshot(tmp_path,runtime):
    _,claim,source=start(tmp_path,runtime)
    with source.transaction() as db:
        state=json.loads(db.execute('SELECT body FROM v3_source_state WHERE job_id=?',(claim['id'],)).fetchone()[0])
        state['evidence']['E1']['result']['case_id']='foreign-case';state['evidence']['E1']['sha256']=digest(state['evidence']['E1']['result'])
        db.execute('UPDATE v3_source_state SET body=?,hash=? WHERE job_id=?',(encoded(state),state_digest(state),claim['id']))
    with pytest.raises(Blocked,match='source_evidence_scope'):source.evidence()


def test_operation_replay_preserves_original_tool_result_without_another_attempt(tmp_path,runtime):
    _,claim,source=start(tmp_path,runtime);identity=next(r['source_id'] for r in runtime['records'] if r['source_type']=='authentication')
    original=source.call('v3_read',dict(source_ids=[identity]),operation_key='host-request-1')
    restarted=SourceAccess(source.path,claim,runtime)
    replay=restarted.call('v3_read',dict(source_ids=[identity]),operation_key='host-request-1')
    assert replay['operation_replayed'] and replay['frame']==original['frame'] and restarted.position()['tool_attempts']==1
    with pytest.raises(Blocked,match='source_operation_request_changed'):restarted.call('v3_context',{},operation_key='host-request-1')
    restarted.advance(1)
    with pytest.raises(Blocked,match='source_operation_time_changed'):restarted.call('v3_read',dict(source_ids=[identity]),operation_key='host-request-1')


def test_new_v3_mcp_roundtrip_and_typed_delivery(tmp_path,runtime):
    from app.investigation.v3.source_transport import connect,call
    store,claim,source=start(tmp_path,runtime);snapshot=tmp_path/'runtime.json';snapshot.write_text(json.dumps(runtime),encoding='utf-8')
    async def acquire():
        with (tmp_path/'mcp.log').open('w',encoding='utf-8') as log:
            async with connect(snapshot,runtime,source.path,claim,errlog=log) as (session,handshake):
                assert set(handshake['tools'])=={'v3_context','v3_read'}
                context=await call(session,'v3_context',{});ids=[s['source_id'] for s in context['catalog']['sources']]
                result=await call(session,'v3_read',dict(source_ids=ids));assert result['ok']
                assert (await call(session,'v3_read',dict(source_ids=ids)))['frame'] is None
                future=next(r['source_id'] for r in runtime['records'] if r['available_at']>runtime['stage_times'][2])
                assert (await call(session,'v3_read',dict(source_ids=[future])))['error']['code']=='source_not_available'
    asyncio.run(acquire());acquired=ledger(runtime,source)
    auth=next(k for k,v in acquired.lines.items() if v['source_type']=='authentication');target=acquired.target_id
    facts=dict(facts=[dict(predicate='authentication',value='success',refs=[auth])])
    report=dict(investigation=dict(hypotheses=[dict(description='Intent remains unknown',status='open',support=['F1'],counterevidence=[],unknowns=['Customer statement'])],
        tensions=[],timeline_refs=list(acquired.events),recommendations=[dict(action='analyst_review',fact_ids=['F1'],reason='Scripted protocol probe')],missing=['customer_statement'],stop_reason='unavailable_evidence',summary='Author simulation; not a model-quality claim'),
        source_statement=dict(value='unresolved',fact_ids=[],rationale='No customer original read at T0'),proposed_disposition='continue_collection')
    client=Client([facts,report]);result=StagedDelivery(HarnessStore(source.path),claim,acquired,client,max_requests=2,report_contract=TypedReviewContract(target)).run()
    assert result['source_statement']['value']=='unresolved' and client.calls==2 and len(source.evidence())==2
