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


def extended_ledger(path):
    from app.investigation.contracts import digest
    a=authorization();amendment=dict(state='approved',grant_message='unit approval 10',base_authorization_hash=digest(a),max_estimated_cny='10')
    extension=dict(state='approved',grant_message='unit approval 13/28',base_authorization_hash=digest(a),previous_amendment_hash=digest(amendment),max_estimated_cny='13',project_limit_cny='28')
    return WorldLedger(path,a,budget_amendment=amendment,budget_extension=extension),a,amendment,extension


def test_13_28_requires_unchanged_original_and_chained_explicit_grants(tmp_path):
    ledger,a,amendment,extension=extended_ledger(tmp_path/'extended.sqlite')
    assert ledger.batch_limit==Decimal('13') and ledger.project_limit==Decimal('28')
    assert a['max_estimated_cny']=='8' and a['project_limit_cny']=='25' and amendment['max_estimated_cny']=='10'
    for field,value in [('previous_amendment_hash','0'*64),('project_limit_cny','29'),('grant_message','')]:
        changed={**extension,field:value}
        with pytest.raises(ValueError):WorldLedger(tmp_path/'invalid.sqlite',a,budget_amendment=amendment,budget_extension=changed)
    with pytest.raises(ValueError):WorldLedger(tmp_path/'missing.sqlite',a,budget_extension=extension)
    run=ledger.prefix+'main_eval:finance:world-031:B2'
    original=WorldLedger(tmp_path/'original.sqlite',a,budget_amendment=amendment)
    with pytest.raises(ValueError):original.reserve(run,Decimal('.01'),run_budget='.5',project_budget='28',max_requests=6)
    with pytest.raises(ValueError):ledger.reserve(run,Decimal('.01'),run_budget='.5',project_budget='28',max_requests=7)


def test_extended_round_and_shared_project_limits_still_stop_spending(tmp_path):
    ledger,_,_,_=extended_ledger(tmp_path/'round.sqlite')
    import sqlite3
    with sqlite3.connect(ledger.path) as db:
        db.execute('INSERT INTO budget_holds VALUES (?,?,?,?)',('prior',ledger.prefix+'development:prior:world-001:B2','12.95','unit conservative risk hold'))
    run=ledger.prefix+'main_eval:finance:world-031:B2'
    first=ledger.reserve(run,Decimal('.04'),run_budget='.5',project_budget='28',max_requests=6);ledger.settle(first,estimate='.04',usage={})
    with pytest.raises(BudgetExceeded,match='upgrade_estimated'):ledger.reserve(run,Decimal('.02'),run_budget='.5',project_budget='28',max_requests=6)
    shared,_,_,_=extended_ledger(tmp_path/'shared.sqlite')
    with sqlite3.connect(shared.path) as db:
        db.execute('INSERT INTO budget_holds VALUES (?,?,?,?)',('legacy','another-project-run','27.95','unit prior project spend'))
    first=shared.reserve(shared.prefix+'main_eval:finance:world-031:B2',Decimal('.04'),run_budget='.5',project_budget='28',max_requests=6);shared.settle(first,estimate='.04',usage={})
    with pytest.raises(BudgetExceeded,match='estimated_spend_limit'):shared.reserve(shared.prefix+'main_eval:finance:world-032:B2',Decimal('.02'),run_budget='.5',project_budget='28',max_requests=6)


def test_financial_extension_changes_only_project_policy_field(tmp_path):
    import json
    from pathlib import Path
    from app.investigation.contracts import digest
    from app.evaluation.world_model_runner import execution_policy
    _,a,amendment,_=extended_ledger(tmp_path/'policy-ledger.sqlite')
    a['policy']=json.loads((Path(__file__).resolve().parents[1]/'config/siliconflow-world-v2.json').read_text())
    amendment['base_authorization_hash']=digest(a)
    extension=dict(state='approved',grant_message='unit approval 13/28',base_authorization_hash=digest(a),previous_amendment_hash=digest(amendment),max_estimated_cny='13',project_limit_cny='28')
    (tmp_path/'budget-amendment.json').write_text(json.dumps(amendment))
    (tmp_path/'budget-extension.json').write_text(json.dumps(extension))
    before=a['policy'];after=execution_policy(tmp_path,a)
    assert {k for k in before if before[k]!=after[k]}=={'project_budget_cny'} and after['project_budget_cny']=='28'


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
