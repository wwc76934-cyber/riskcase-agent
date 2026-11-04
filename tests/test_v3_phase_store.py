import json,sqlite3
import pytest
from tests.test_v3_source_access import runtime,start,ledger
from tests.test_v3_reliability import Client
from app.service.store import Blocked
from app.service.v3_sources import SourceAccess
from app.service.v3_phase_store import DeliveryPhase
from app.investigation.v3.delivery import StagedDelivery
from app.investigation.v3.typed_review import TypedReviewContract


def probe_report():
    return dict(investigation=dict(hypotheses=[dict(description='Scripted phase probe',status='open',support=[],counterevidence=[],unknowns=['Not evaluated'])],
        tensions=[],timeline_refs=[],recommendations=[dict(action='analyst_review',fact_ids=[],reason='Offline only')],missing=[],stop_reason='unavailable_evidence',summary='Not a semantic judgment'),
        source_statement=dict(value='unresolved',fact_ids=[],rationale='Protocol probe'),proposed_disposition='continue_collection')


def test_three_stage_delivery_shares_the_case_lease_without_overwriting_calls_or_usage(tmp_path,runtime):
    _,claim,source=start(tmp_path,runtime);reports=[]
    for stage in range(3):
        if stage:source.advance(stage)
        phase=DeliveryPhase(source,stage);acquired=ledger(runtime,source);client=Client([dict(facts=[]),probe_report()])
        reports.append(StagedDelivery(phase,phase.claim,acquired,client,max_requests=2,report_contract=TypedReviewContract(acquired.target_id)).run())
        assert phase.load(phase.identity)['phase']=='done'
    with sqlite3.connect(source.path) as db:
        assert list(db.execute('SELECT stage,lane,ordinal FROM v3_phase_calls ORDER BY stage,ordinal'))==[(s,'delivery',n) for s in range(3) for n in (1,2)]
        assert db.execute('SELECT COUNT(*) FROM calls').fetchone()[0]==0
        assert db.execute('SELECT COUNT(*) FROM v3_delivery_phase').fetchone()[0]==3
    directory=source.path.parent/'v3-model-records'
    assert len(list(directory.glob('*/usage-*.json')))==6 and len(list(directory.glob('*/response-*.json')))==6
    assert all(report['source_statement']['transaction_id']==runtime['alert']['transaction_id'] for report in reports)
    with pytest.raises(Blocked,match='delivery_source_time_changed'):DeliveryPhase(source,0)


def test_phase_recovery_keeps_parent_journal_and_never_reissues_saved_response(tmp_path,runtime):
    now=[100];store,claim,source=start(tmp_path,runtime,clock=lambda:now[0]);phase=DeliveryPhase(source,0);acquired=ledger(runtime,source)
    def crash(kind,ordinal):
        if kind=='response_saved':raise SystemExit('Probe interruption')
    first=Client([dict(facts=[])])
    with pytest.raises(SystemExit):StagedDelivery(phase,phase.claim,acquired,first,max_requests=2,report_contract=TypedReviewContract(acquired.target_id),lease_seconds=30,hook=crash).run()
    now[0]=131;replacement=store.claim('recovered',lease_seconds=120,deadline_seconds=600,mode='offline')
    resumed_source=SourceAccess(source.path,replacement,runtime,clock=lambda:now[0]);resumed=DeliveryPhase(resumed_source,0);second=Client([probe_report()])
    StagedDelivery(resumed,resumed.claim,acquired,second,max_requests=2,report_contract=TypedReviewContract(acquired.target_id)).run()
    assert first.calls==second.calls==1 and resumed.load(resumed.identity)['replayed_responses']==1


def test_reservation_slots_and_virtual_case_scope_are_not_model_controlled(tmp_path,runtime):
    _,_,source=start(tmp_path,runtime);phase=DeliveryPhase(source,0)
    with pytest.raises(Blocked,match='delivery_phase_scope'):phase.load('another-case-delivery-T0')
    with pytest.raises(Blocked,match='delivery_reserved_slot'):phase.begin_call(phase.identity,phase.claim['owner'],phase.claim['generation'],3,'request')


def test_uncertain_planner_call_blocks_new_paid_slots_in_other_lanes(tmp_path,runtime):
    _,_,source=start(tmp_path,runtime);planner=DeliveryPhase(source,0,lane='planner');delivery=DeliveryPhase(source,0)
    assert planner.begin_call(planner.identity,planner.claim['owner'],planner.claim['generation'],1,'plan-hash') is None
    planner.uncertain_call(planner.identity,planner.claim['owner'],planner.claim['generation'],1,dict(status='unknown'))
    with pytest.raises(Blocked,match='unresolved_case_call'):delivery.begin_call(delivery.identity,delivery.claim['owner'],delivery.claim['generation'],1,'report-hash')
    source.advance(1);later=DeliveryPhase(source,1)
    with pytest.raises(Blocked,match='unresolved_case_call'):later.begin_call(later.identity,later.claim['owner'],later.claim['generation'],1,'later-hash')
