from copy import deepcopy
from datetime import datetime
import pytest
from tests.test_fraud_inquiry import inquiry_snapshot
from app.investigation.contracts import digest
from app.investigation.fraud_world import World,ORIGIN
from app.evaluation.fraud_world_data import make_case,FAMILIES


@pytest.mark.parametrize('family',[f for names in FAMILIES.values() for f in names])
def test_each_mechanism_has_valid_A_B_and_hidden_reference(inquiry_snapshot,family):
    runtime,truth=make_case(inquiry_snapshot,family,0,'world-fixture')
    world=World(runtime)
    assert all(r['origin']==ORIGIN for r in runtime['records'])
    assert truth['runtime_sha256']==digest(runtime)
    assert world.cost('world_materials',{})<=runtime['access_budget_units']
    assert 'expected_action' not in runtime and 'family' not in runtime
    assert world.call('world_materials',{})['data']['A']['rows']==runtime['transaction_snapshot']['rows']
    assert set(truth['eligible_source_ids'])==set(world.eligible)


def test_pair_keeps_initial_alert_and_changes_followup(inquiry_snapshot):
    first,a=make_case(inquiry_snapshot,'auth_denial',0,'case-a')
    second,b=make_case(inquiry_snapshot,'auth_denial',1,'case-b')
    assert first['transaction_snapshot']['rows']==second['transaction_snapshot']['rows']
    assert first['transaction_snapshot']['alert']==second['transaction_snapshot']['alert']
    assert a['expected_action']!=b['expected_action']
    assert b['tension']=='authentication_is_not_authorization'
    facts={r['predicate']:r['value'] for r in b['expected_claims']}
    assert facts['authentication']=='success' and facts['customer_authorization']=='denied'


def test_later_available_authentication_is_never_returned(inquiry_snapshot):
    runtime,truth=make_case(inquiry_snapshot,'late_auth',0,'late')
    world=World(runtime)
    assert world.call('world_sources',{'source_type':'authentication'})['data']['missing']
    future=truth['future_excluded_source_ids'];assert len(future)==1
    with pytest.raises(PermissionError):world.call('world_read',{'source_id':future[0]})
    assert future[0] not in str(world.call('world_materials',{}))
    with pytest.raises(PermissionError):world.call('world_read',{'source_id':'../controller/truth.json'})


@pytest.mark.parametrize('where',['top','record'])
def test_truth_and_origin_pollution_rejected(inquiry_snapshot,where):
    runtime,_=make_case(inquiry_snapshot,'auth_denial',0,'test')
    if where=='top':runtime['expected_action']='safe_close'
    else:
        runtime['records'][0]['gold']='fraud';runtime['records_sha256']=digest(runtime['records'])
    with pytest.raises(ValueError):World(runtime)


def test_version_change_and_late_retraction_do_not_rewrite_history(inquiry_snapshot):
    runtime,_=make_case(inquiry_snapshot,'policy_change',0,'policy')
    world=World(runtime)
    versions=[world.original(k) for k,r in world.eligible.items() if r['source_type']=='procedure']
    assert {v['version']:v['effective_at_investigation'] for v in versions}=={1:False,2:True}
    runtime,truth=make_case(inquiry_snapshot,'customer_retraction',1,'retraction')
    world=World(runtime)
    assert len(truth['future_excluded_source_ids'])==1
    initial=next(k for k,r in world.eligible.items() if r['source_type']=='customer')
    assert world.original(initial)['superseded_by_available']==[]


def test_document_after_transaction_can_be_used_only_at_investigation_time(inquiry_snapshot):
    runtime,_=make_case(inquiry_snapshot,'delegated_user',0,'time')
    world=World(runtime)
    customer=next(r for r in world.eligible.values() if r['source_type']=='customer')
    assert runtime['cutoff']<customer['event_time']<=runtime['investigation_as_of']
    target=runtime['transaction_snapshot']['target_id']
    assert all(r['event_time']<runtime['cutoff'] for r in runtime['transaction_snapshot']['rows'] if r['transaction_id']!=target)


def test_export_cannot_claim_future_coverage(inquiry_snapshot):
    runtime,_=make_case(inquiry_snapshot,'auth_denial',0,'coverage')
    runtime['records'][0]['coverage']['end']='2020-01-01T00:00:00'
    runtime['records_sha256']=digest(runtime['records'])
    with pytest.raises(ValueError,match='export'):World(runtime)
