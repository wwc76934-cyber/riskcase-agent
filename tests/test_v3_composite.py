import pytest
from tests.test_v3_source_access import runtime,start
from tests.test_v3_reliability import Client
from tests.test_v3_phase_store import probe_report
from app.service.v3_phase_store import DeliveryPhase
from app.service.v3_sources import SourceAccess
from app.investigation.v3.rule_workflow import collect_rule_workflow
from app.investigation.v3.typed_review import TypedReviewContract
from app.investigation.v3.composite_delivery import CompositeDelivery


def payload(ledger):
    handle=next(k for k,v in ledger.lines.items() if v['source_type']=='authentication')
    return dict(extraction=dict(facts=[dict(predicate='authentication',value='success',refs=[handle])]),report=probe_report())


def test_one_shot_baseline_delivers_full_current_sources_in_one_request(tmp_path,runtime):
    _,_,source=start(tmp_path,runtime);acquired=collect_rule_workflow(source);phase=DeliveryPhase(source,0);client=Client([payload(acquired)])
    report=CompositeDelivery(phase,acquired,client,TypedReviewContract(acquired.target_id)).run()
    assert client.calls==1 and report['facts'][0]['predicate']=='authentication' and report['human_review_required']


def test_known_protocol_failure_has_one_bounded_repair_and_preserves_original(tmp_path,runtime):
    _,_,source=start(tmp_path,runtime);acquired=collect_rule_workflow(source);phase=DeliveryPhase(source,0)
    client=Client([dict(not_the_contract=True),payload(acquired)])
    CompositeDelivery(phase,acquired,client,TypedReviewContract(acquired.target_id)).run()
    state=phase.load(phase.identity)
    assert client.calls==2 and len(state['raw_responses'])==2 and len(state['protocol_errors'])==1
    assert 'not_the_contract' in state['raw_responses'][0]['content']


def test_received_composite_response_is_reused_after_process_interruption(tmp_path,runtime):
    now=[100];store,_,source=start(tmp_path,runtime,clock=lambda:now[0]);acquired=collect_rule_workflow(source);phase=DeliveryPhase(source,0)
    def crash(kind,ordinal):raise SystemExit('After paid response journal')
    first=Client([payload(acquired)])
    with pytest.raises(SystemExit):CompositeDelivery(phase,acquired,first,TypedReviewContract(acquired.target_id),hook=crash).run()
    now[0]=221;replacement=store.claim('replacement',lease_seconds=120,deadline_seconds=600,mode='offline')
    resumed=DeliveryPhase(SourceAccess(source.path,replacement,runtime,clock=lambda:now[0]),0);second=Client([])
    CompositeDelivery(resumed,acquired,second,TypedReviewContract(acquired.target_id)).run()
    assert first.calls==1 and second.calls==0 and resumed.load(resumed.identity)['replayed_responses']==1
