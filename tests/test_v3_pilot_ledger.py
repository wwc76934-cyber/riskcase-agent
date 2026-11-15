from decimal import Decimal
import hashlib,json,sqlite3
import pytest
from app.evaluation.v3_pilot_ledger import PilotLedger
from app.llm.cost_ledger import BudgetExceeded


def fixture(tmp_path):
    proposal=dict(schema='v3-new-development-delivery-pilot-v1',state='pending_user_authorization',grant_message=None,max_estimated_cny='6',project_limit_cny='34',project_before_pilot_committed_cny='0',
        max_case_runs=12,max_phase_jobs=36,max_requests=72,repeats=2,max_requests_per_phase=2,max_requests_per_case_run=6,
        case_ids=[f'v3-{i:018x}' for i in range(6)],model='zai-org/GLM-5.3-Flash',max_case_estimated_cny='.50')
    path=tmp_path/'proposal.json';path.write_text(json.dumps(proposal),encoding='utf-8')
    grant=dict(state='approved',grant_message='UNIT TEST ONLY, not a real model authorization',proposal_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),max_estimated_cny='6',project_limit_cny='34')
    return path,proposal,grant


def ledger(tmp_path):
    path,proposal,grant=fixture(tmp_path)
    return PilotLedger(tmp_path/'ledger.sqlite',path,grant),proposal,path


def reserve(l,run,amount='.06'):return l.reserve(run,Decimal(amount),run_budget='.50',project_budget='34',max_requests=2)


def test_missing_or_old_scope_grant_never_creates_a_pilot_ledger(tmp_path):
    path,_,grant=fixture(tmp_path);grant['state']='pending'
    with pytest.raises(ValueError,match='new user grant'):PilotLedger(tmp_path/'not-created.sqlite',path,grant)
    assert not (tmp_path/'not-created.sqlite').exists()
    grant['state']='approved';grant['max_estimated_cny']='13'
    with pytest.raises(ValueError):PilotLedger(tmp_path/'not-created.sqlite',path,grant)


def test_bound_file_and_in_memory_limits_cannot_drift(tmp_path):
    l,p,path=ledger(tmp_path);run=l.run_id(p['case_ids'][0],1,0)
    assert l.validated_project_limit(run)==Decimal('34')
    l.project_limit=Decimal('35')
    with pytest.raises(ValueError,match='limits changed'):l.validated_project_limit(run)
    l.project_limit=Decimal('34');path.write_bytes(path.read_bytes()+b' ')
    with pytest.raises(ValueError,match='proposal changed'):reserve(l,run)


def test_only_the_36_planned_phase_scopes_and_two_requests_are_allowed(tmp_path):
    l,p,_=ledger(tmp_path);case=p['case_ids'][0]
    for stage in range(3):
        run=l.run_id(case,1,stage)
        for _ in range(2):identity=reserve(l,run);l.settle(identity,estimate='.01',usage={})
        with pytest.raises(BudgetExceeded,match='request_limit'):reserve(l,run)
    for run in [l.run_id('old-world-031',1,0),l.run_id(case,3,0),l.run_id(case,1,3)]:
        with pytest.raises(ValueError):reserve(l,run)
    with pytest.raises(ValueError,match='profile'):reserve(l,l.run_id(case,2,0),'.064')


def test_terminal_failed_phase_cannot_be_retried_or_relabelled(tmp_path):
    l,p,_=ledger(tmp_path);run=l.run_id(p['case_ids'][0],1,0)
    identity=reserve(l,run);l.settle(identity,estimate='.01',usage={});l.close_phase(run,'model_failure')
    with pytest.raises(BudgetExceeded,match='terminal_phase_no_retry'):reserve(l,run)
    with pytest.raises(ValueError,match='relabeled'):l.close_phase(run,'delivered')
    l.close_phase(run,'model_failure')
    identity=reserve(l,l.run_id(p['case_ids'][0],2,0));l.settle(identity,estimate='.01',usage={})


@pytest.mark.parametrize('scope',['case','round','project'])
def test_shared_case_round_and_project_holds_all_block_spend(tmp_path,scope):
    l,p,_=ledger(tmp_path);run=l.run_id(p['case_ids'][0],1,0);case=l.parts(run)
    key,amount=(case,'.49') if scope=='case' else (l.prefix+'another:run','5.99') if scope=='round' else ('another-project','33.99')
    with sqlite3.connect(l.path) as db:db.execute('INSERT INTO budget_holds VALUES (?,?,?,?)',('prior',key,amount,'Unit conservative prior hold'))
    with pytest.raises(BudgetExceeded):reserve(l,run,'.02')


def test_uncertainty_holds_whole_case_including_prior_known_spend(tmp_path):
    l,p,_=ledger(tmp_path);case=p['case_ids'][0];run=l.run_id(case,1,0)
    first=reserve(l,run);l.settle(first,estimate='.02',usage={})
    unknown=reserve(l,run);l.settle(unknown)
    other=l.run_id(p['case_ids'][1],1,0)
    with pytest.raises(BudgetExceeded,match='requires_review'):reserve(l,other)
    receipt=dict(request_audit=[dict(reservation_id=unknown,error_code='transport_error_unknown_charge')])
    body=l.review_transport_uncertainty(unknown,receipt,reviewer='unit-operator',reason='Individually inspected timeout; no retry; retain unknown charge and whole case allowance')
    assert Decimal(body['additional_hold_cny'])==Decimal('.42')
    assert l.summary(run)['case_committed_estimate_cny']=='0.50'
    assert l.summary(run)['unknown_cost_requests']==1
    with pytest.raises(BudgetExceeded,match='case_limit'):reserve(l,l.run_id(case,1,1))
    identity=reserve(l,other);l.settle(identity,estimate='.01',usage={})
    assert l.review_transport_uncertainty(unknown,receipt,reviewer='unit-operator',reason='Same inspected timeout; not an additional waiver or fee hold')==body


def test_unexpected_model_or_new_uncertainty_is_not_covered_by_an_old_review(tmp_path):
    l,p,_=ledger(tmp_path);run=l.run_id(p['case_ids'][0],1,0);unknown=reserve(l,run);l.settle(unknown)
    receipt=dict(request_audit=[dict(reservation_id=unknown,error_code='transport_error_unknown_charge',returned_model='unexpected-model')])
    with pytest.raises(ValueError,match='Only inspected'):l.review_transport_uncertainty(unknown,receipt,reviewer='unit',reason='Model changes cannot be treated as ordinary transport uncertainty')
