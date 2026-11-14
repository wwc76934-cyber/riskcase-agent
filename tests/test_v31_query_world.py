from copy import deepcopy
from datetime import datetime,timedelta
import asyncio
import json
import pytest
from tests.test_v3_source_access import runtime,start,ledger
from app.investigation.contracts import digest
from app.investigation.v3.world import InvestigationWorld
from app.investigation.v3.query_world import SourceQueryWorld,CONTRACT,world_type
from app.investigation.v3.source_roles import BUSINESS_ROLES
from app.investigation.v3.output import OutputFailure
from app.investigation.v3.source_transport import call,connect
from app.investigation.v3.typed_review import TypedReviewContract
from app.investigation.v3.v31_output import V31ReviewContract
from app.investigation.v3.rule_workflow import readable_batch,rule_report
from app.investigation.v3.trajectory import run_case
from app.service.v3_sources import SourceAccess
from tests.test_v3_trajectory import AdaptiveProtocolClient
from tests.test_v3_native_planner import read_call
from app.evaluation.v31_query_development import callback_and_capture
from app.investigation.v3.mcp_trajectory import run_case_mcp

@pytest.fixture
def query_runtime(runtime):
    s=deepcopy(runtime);s['contract']=CONTRACT
    s['query_rules']={r['source_id']:dict(business_role=sorted(BUSINESS_ROLES[r['source_type']])[0],expires_at=None,query_outcome='available') for r in s['records']}
    return s

def timed_pair(snapshot):
    current=[r for r in snapshot['records'] if r['available_at']<=snapshot['stage_times'][0] and not r['requires']][:2]
    assert len(current)==2
    for r,cost,latency in zip(current,[2,3],[2,3]):r.update(cost_units=cost,latency_minutes=latency)
    snapshot['query_rules'][current[1]['source_id']]['expires_at']=(datetime.fromisoformat(snapshot['stage_times'][0])+timedelta(minutes=4)).isoformat()
    snapshot['records_sha256']=digest(snapshot['records'])
    return [r['source_id'] for r in current]

def test_old_contract_and_results_are_unchanged_and_new_rules_are_versioned(runtime,query_runtime):
    assert world_type(runtime) is InvestigationWorld and world_type(query_runtime) is SourceQueryWorld
    with pytest.raises(ValueError):InvestigationWorld(query_runtime)
    bad=deepcopy(runtime);bad['query_rules']=query_runtime['query_rules']
    with pytest.raises(ValueError):world_type(bad)(bad)
    assert 'source_query_results' not in InvestigationWorld(runtime).catalog()

def test_actual_query_completion_enforces_expiry_and_order(query_runtime):
    ids=timed_pair(query_runtime);world=SourceQueryWorld(query_runtime)
    result=world.read(ids)
    assert [r['status'] for r in result['data']['query_results']]==['available','expired']
    assert [r['source_id'] for r in result['data']['B']]==ids[:1]
    assert world.spent==5 and world.elapsed[0]==5
    reverse=SourceQueryWorld(query_runtime);assert len(reverse.read(list(reversed(ids)))['data']['B'])==2
    assert SourceQueryWorld.restore(query_runtime,world.checkpoint()).checkpoint()==world.checkpoint()

def test_unavailable_query_consumes_declared_resources_but_has_no_original(query_runtime):
    ids=timed_pair(query_runtime);identity=ids[0]
    query_runtime['query_rules'][identity]['query_outcome']='unavailable'
    world=SourceQueryWorld(query_runtime);result=world.read([identity])
    assert result['data']['B']==[] and result['data']['query_results'][0]['status']=='unavailable'
    assert not world.acquired and world.spent==2 and world.elapsed[0]==2
    assert identity not in {s['source_id'] for s in world.catalog()['sources']}
    before=world.checkpoint();again=world.read([identity])
    assert again['data']['access_cost_units']==0 and world.checkpoint()==before

@pytest.mark.parametrize('which',['cost','time'])
def test_batch_admission_is_atomic_and_cannot_spend_past_budget(query_runtime,which):
    ids=timed_pair(query_runtime)
    if which=='cost':query_runtime['access_budget_units']=4
    else:query_runtime['stage_windows_minutes'][0]=4
    world=SourceQueryWorld(query_runtime);before=world.checkpoint()
    with pytest.raises(OutputFailure):world.read(ids)
    assert world.checkpoint()==before and world.spent==0 and not world.queried

def test_checkpoint_binds_query_rules_and_receipt_types(query_runtime):
    ids=timed_pair(query_runtime);world=SourceQueryWorld(query_runtime);world.read([ids[0]])
    checkpoint=world.checkpoint();bad=deepcopy(checkpoint);bad['receipts'][0]['cost_units']=True
    with pytest.raises(ValueError):SourceQueryWorld.restore(query_runtime,bad)
    changed=deepcopy(query_runtime);changed['query_rules'][ids[0]]['query_outcome']='unavailable'
    with pytest.raises(ValueError):SourceQueryWorld.restore(changed,checkpoint)

def test_evidence_replay_rejects_a_fabricated_original_for_unavailable_source(query_runtime):
    ids=timed_pair(query_runtime);identity=ids[0];query_runtime['query_rules'][identity]['query_outcome']='unavailable'
    world=SourceQueryWorld(query_runtime);frame=world.read([identity])
    evidence={'E1':dict(result=world.overview()),'E2':dict(result=frame)}
    world.verify_evidence(evidence,{identity:'E2'},world.checkpoint())
    corrupt=deepcopy(evidence);corrupt['E2']['result']['data']['B']=[dict(source_id=identity,text='Fabricated original')]
    with pytest.raises(ValueError):world.verify_evidence(corrupt,{identity:'E2'},world.checkpoint())

def test_rules_cannot_smuggle_reference_answers_or_invalid_roles(query_runtime):
    identity=next(iter(query_runtime['query_rules']))
    for key,value in [('expected_state','authorized'),('business_role',['not-a-role'])]:
        bad=deepcopy(query_runtime);bad['query_rules'][identity][key]=value
        with pytest.raises(ValueError):SourceQueryWorld(bad)


def test_real_mcp_records_missing_and_expired_without_creating_citations(tmp_path,query_runtime):
    missing,expires=timed_pair(query_runtime)
    query_runtime['query_rules'][missing]['query_outcome']='unavailable'
    _,claim,source=start(tmp_path,query_runtime)
    snapshot=tmp_path/'runtime.json'
    snapshot.write_text(json.dumps(query_runtime),encoding='utf-8')

    async def run():
        with (tmp_path/'mcp.log').open('w',encoding='utf-8') as log:
            async with connect(snapshot,query_runtime,source.path,claim,errlog=log) as (session,_):
                args={'source_ids':[missing,expires]}
                first=await call(session,'v3_read',args,operation_key='batch-1')
                replay=await call(session,'v3_read',args,operation_key='batch-1')
                cached=await call(session,'v3_read',args)
                return first,replay,cached

    first,replay,cached=asyncio.run(run())
    assert first['ok'] and first['frame']['data']['B']==[]
    assert [q['status'] for q in first['frame']['data']['query_results']]==['unavailable','expired']
    assert replay['operation_replayed'] and replay['evidence_id']==first['evidence_id']
    assert cached['frame'] is None
    restarted=SourceAccess(source.path,claim,query_runtime)
    assert restarted.position()['queried_source_ids']==sorted([missing,expires])
    assert restarted.position()['acquired_source_ids']==[]
    acquired=ledger(query_runtime,restarted)
    assert not acquired.lines and all(event['layer']=='A' for event in acquired.events.values())
    assert set(acquired.model_context()['source_query_results'])=={missing,expires}


def test_relay_cannot_be_used_as_definite_cardholder_statement(tmp_path,query_runtime):
    customer=next(record for record in query_runtime['records']
                  if record['source_type']=='customer' and not record['requires']
                  and record['available_at']<=query_runtime['stage_times'][2])
    identity=customer['source_id']
    query_runtime['query_rules'][identity]['business_role']='third_party_relay'
    _,_,source=start(tmp_path,query_runtime)
    for stage in (1,2):
        if customer['available_at']>source.position()['as_of']:source.advance(stage)
    assert source.call('v3_read',dict(source_ids=[identity]))['ok']
    acquired=ledger(query_runtime,source)
    reference=next(h for h,line in acquired.lines.items() if line['source_id']==identity)
    assert acquired.lines[reference]['business_role']=='third_party_relay'
    for factory in (TypedReviewContract,V31ReviewContract):
        contract=factory(acquired.target_id)
        claim=dict(predicate='customer_authorization',value='authorized',refs=[reference])
        with pytest.raises(OutputFailure):contract.extract(dict(facts=[claim]),acquired)
        claim['value']='unresolved'
        assert contract.extract(dict(facts=[claim]),acquired)['facts']['F1']['value']=='unresolved'
    assert rule_report(acquired)['source_statement']['value']=='unresolved'


def test_full_material_baseline_schedules_tight_deadline_first(query_runtime):
    later,urgent=timed_pair(query_runtime)
    catalog=SourceQueryWorld(query_runtime).catalog()
    candidates=[s for s in catalog['sources'] if s['source_id'] in (later,urgent)]
    assert [s['source_id'] for s in readable_batch(catalog,candidates)]==[urgent,later]


def test_query_window_cannot_overlap_next_stage(query_runtime):
    query_runtime['stage_windows_minutes'][0]=51
    with pytest.raises(ValueError,match='overlaps'):
        SourceQueryWorld(query_runtime)


def test_hybrid_uses_same_deterministic_acquisition_as_code(tmp_path,query_runtime):
    _,_,b0=start(tmp_path/'b0',query_runtime)
    _,_,b4=start(tmp_path/'b4',query_runtime)
    baseline=run_case(b0,'B0')
    hybrid=run_case(b4,'B4',lambda stage:AdaptiveProtocolClient())
    assert all(p['report'] for p in baseline['phases']+hybrid['phases'])
    assert hybrid['planning_protocol_attempts']==0
    assert [p['evidence'] for p in hybrid['phases']]==[p['evidence'] for p in baseline['phases']]


@pytest.mark.parametrize('callback_available',[True,False])
def test_scripted_agent_replans_after_real_mcp_query_outcome(tmp_path,callback_available):
    snapshot,reference=callback_and_capture(callback_available=callback_available)
    _,_,source=start(tmp_path,snapshot)
    artifact=tmp_path/'runtime.json'
    artifact.write_text(json.dumps(snapshot,ensure_ascii=False),encoding='utf-8')

    class ScriptedPlanner(AdaptiveProtocolClient):
        def chat(self,messages,tools=None):
            if not tools:return super().chat(messages,tools)
            context=json.loads(messages[1]['content']);catalog=context['catalog']
            allowed=set(tools[0]['function']['parameters']['properties']['source_ids']['items']['enum'])
            sources={r['channel_ref']:r['source_id'] for r in catalog['sources'] if r['source_id'] in allowed}
            if catalog['stage']!=1:
                return super().chat(messages,tools)
            trace=context['planning_trace']
            if not trace:return read_call([sources['routing'],sources['relay']])
            if len(trace)==1:return read_call([sources['registered-callback']])
            if len(trace)==2:
                status=next(iter(trace[-1]['query_outcomes'].values()))
                channel='capture-ledger' if status=='available' else 'app-attestation'
                return read_call([sources[channel]])
            return dict(content='Scripted ready for report')

    async def run():
        with (tmp_path/'mcp.log').open('w',encoding='utf-8') as log:
            return await run_case_mcp(source,artifact,'B2',lambda _:ScriptedPlanner(),errlog=log)

    result=asyncio.run(run())
    assert result['complete'] and result['source_transport']=='official_mcp_stdio'
    assert result['evidence_kind']=='scripted_protocol_probe'
    assert all(phase['report'] for phase in result['phases'])
    trace=result['phases'][1]['plan']['trace']
    assert [step['source_ids'][0] for step in trace[1:]]==reference['required_T1_queries']
    assert reference['required_T2_original'] in result['phases'][2]['source_state']['acquired_source_ids']

    # The same constraints allow the deterministic baseline to reach this
    # evidence. A scripted adaptive trace alone does not show an advantage.
    _,_,baseline=start(tmp_path/'baseline',snapshot)
    rule_result=run_case(baseline,'B0')
    at_t1=rule_result['phases'][1]['source_state']['catalog']['source_query_results']
    assert set(reference['required_T1_queries'])<=set(at_t1)
