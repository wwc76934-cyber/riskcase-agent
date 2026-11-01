from copy import deepcopy
from decimal import Decimal
import pytest
from app.llm.world_ledger import WorldLedger
from app.llm.cost_ledger import BudgetExceeded


def authorization():
    return dict(id='1'*32,state='approved',grant_message='unit test approval, no external network',max_estimated_cny='8',project_limit_cny='25',
                groups=dict(development=dict(case_ids=[f'world-{i:03d}' for i in range(1,31)],conditions=['B1','B2'],max_runs=24),
                            main_eval=dict(case_ids=[f'world-{i:03d}' for i in range(31,91)],conditions=['B1','B2','B3'],max_runs=180),
                            memory_eval=dict(case_ids=[f'world-{i:03d}' for i in range(71,91)],conditions=['raw_history','stale','wrong'],max_runs=60)))


def test_no_permission_or_expanded_scope_is_not_accepted(tmp_path):
    a=authorization();a['state']='pending_user_authorization'
    with pytest.raises(ValueError):WorldLedger(tmp_path/'x.sqlite',a)


def test_budget_increase_requires_separate_bound_receipt_without_rewriting_original(tmp_path):
    from app.investigation.contracts import digest
    a=authorization();ledger=WorldLedger(tmp_path/'ledger.sqlite',a)
    amendment=dict(state='approved',grant_message='unit test only: authorize 10 CNY',base_authorization_hash=digest(a),max_estimated_cny='10')
    raised=WorldLedger(ledger.path,a,budget_amendment=amendment)
    assert raised.batch_limit==Decimal('10') and a['max_estimated_cny']=='8'
    amendment['max_estimated_cny']='11'
    with pytest.raises(ValueError):WorldLedger(ledger.path,a,budget_amendment=amendment)
    a=authorization();a['groups']['development']['max_runs']=25
    with pytest.raises(ValueError):WorldLedger(tmp_path/'x.sqlite',a)


def test_global_existing_spend_scope_and_unknown_cost_are_enforced(tmp_path):
    ledger=WorldLedger(tmp_path/'ledger.sqlite',authorization())
    prefix=ledger.prefix
    for suffix in ['main_eval:p1:world-001:B2','development:p1:world-001:B3']:
        with pytest.raises(ValueError):ledger.register(prefix+suffix)
    run=prefix+'development:p1:world-001:B2'
    reservation=ledger.reserve(run,Decimal('.1'),run_budget='.5',project_budget='25',max_requests=6)
    with pytest.raises(BudgetExceeded,match='unknown'):ledger.reserve(run,Decimal('.1'),run_budget='.5',project_budget='25',max_requests=6)
    ledger.settle(reservation,estimate='.02',usage={})
    ledger.reserve(run,Decimal('.1'),run_budget='.5',project_budget='25',max_requests=6)


def test_development_reruns_consume_same_24_slots(tmp_path):
    ledger=WorldLedger(tmp_path/'ledger.sqlite',authorization())
    for i in range(24):ledger.register(ledger.prefix+f'development:repair-{i}:world-001:B2')
    with pytest.raises(BudgetExceeded,match='group_case'):ledger.register(ledger.prefix+'development:repair-25:world-001:B2')
    # Already registered retries do not manufacture a new slot.
    ledger.register(ledger.prefix+'development:repair-0:world-001:B2')


def test_uncertain_transport_needs_individual_review_and_keeps_full_case_hold(tmp_path):
    from app.llm.cost_ledger import CostLedger
    ledger=WorldLedger(tmp_path/'ledger.sqlite',authorization());first=ledger.prefix+'development:p1:world-001:B2'
    identity=ledger.reserve(first,Decimal('.06'),run_budget='.5',project_budget='25',max_requests=6);ledger.settle(identity)
    next_run=ledger.prefix+'development:p2:world-002:B2'
    with pytest.raises(BudgetExceeded,match='unknown'):ledger.reserve(next_run,Decimal('.01'),run_budget='.5',project_budget='25',max_requests=6)
    receipt=dict(request_audit=[dict(reservation_id=identity,error_code='transport_error_unknown_charge')])
    review=ledger.review_transport_uncertainty(identity,receipt,reason='Inspect a terminal timeout; preserve unknown charge and reserve the whole case allowance',reviewer='test-operator')
    assert review['provider_charge_still_unknown'] and review['additional_hold_cny']=='0.44'
    summary=CostLedger(ledger.path).summary(first)
    assert summary['unknown_cost_requests']==1 and Decimal(summary['run_committed_estimate_cny'])==Decimal('.50')
    with pytest.raises(BudgetExceeded):ledger.reserve(first,Decimal('.01'),run_budget='.5',project_budget='25',max_requests=6)
    other=ledger.reserve(next_run,Decimal('.01'),run_budget='.5',project_budget='25',max_requests=6)
    # A newly uncertain request is never covered by the earlier operator review.
    ledger.settle(other)
    with pytest.raises(BudgetExceeded,match='unknown'):ledger.reserve(next_run,Decimal('.01'),run_budget='.5',project_budget='25',max_requests=6)
    receipt['request_audit'][0]['error_code']='returned_model_changed_unknown_pricing'
    with pytest.raises(ValueError):ledger.review_transport_uncertainty(other,receipt,reason='Cannot approve an unexpected returned model as if it were the agreed model',reviewer='test-operator')
