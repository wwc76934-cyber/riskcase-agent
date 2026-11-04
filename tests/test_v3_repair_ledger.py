from copy import deepcopy
import hashlib,json,sqlite3
from decimal import Decimal
import pytest
from tests.test_v3_pilot_ledger import fixture,reserve
from app.evaluation.v3_pilot_ledger import PilotLedger
from app.investigation.contracts import digest


def setup(tmp_path):
    original,body,grant=fixture(tmp_path);grant_path=tmp_path/'original-grant.json';grant_path.write_text(json.dumps(grant),encoding='utf-8')
    path=tmp_path/'ledger.sqlite';parent=PilotLedger(path,original,grant)
    identity=reserve(parent,parent.run_id(body['case_ids'][0],1,0));parent.settle(identity,estimate='.01',usage={})
    for case in body['case_ids']:
        for repeat in (1,2):
            for stage in range(3):parent.close_phase(parent.run_id(case,repeat,stage),'model_failure')
    with sqlite3.connect(path) as db:rows=db.execute('SELECT * FROM reservations WHERE run_id LIKE ? ORDER BY id',(parent.prefix+'%',)).fetchall()
    proposal={**body,'schema':'v3-typed-repair-amendment-v1','max_case_runs':6,'max_phase_jobs':18,'max_requests':36,'repeats':1,
        'max_input_bytes':20000,'max_request_reservation_cny':'0.031104','whole_round_limit_cny':'6',
        'parent_scope':dict(identity=parent.identity,proposal_path=str(original),grant_path=str(grant_path),proposal_sha256=hashlib.sha256(original.read_bytes()).hexdigest(),
            grant_sha256=hashlib.sha256(grant_path.read_bytes()).hexdigest(),reservation_rows_sha256=digest([list(r) for r in rows]))}
    repair_path=tmp_path/'repair-proposal.json';repair_path.write_text(json.dumps(proposal),encoding='utf-8')
    repair_grant={**grant,'grant_message':'UNIT TEST explicit additional scope, not actual consent','proposal_sha256':hashlib.sha256(repair_path.read_bytes()).hexdigest()}
    return parent,proposal,repair_path,repair_grant


def test_original_grant_does_not_authorize_a_repair_scope(tmp_path):
    parent,p,path,grant=setup(tmp_path);old=json.loads((tmp_path/'original-grant.json').read_text())
    with pytest.raises(ValueError,match='new user grant'):PilotLedger(parent.path,path,old)
    repair=PilotLedger(parent.path,path,grant)
    with pytest.raises(ValueError):repair.reserve(repair.run_id(p['case_ids'][0],2,0),Decimal('.01'),run_budget='.50',project_budget='34',max_requests=2)


def test_repair_request_bounds_and_original_failures_remain_terminal(tmp_path):
    parent,p,path,grant=setup(tmp_path);repair=PilotLedger(parent.path,path,grant);case=p['case_ids'][0]
    with pytest.raises(ValueError,match='profile'):reserve(repair,repair.run_id(case,1,0),'.04')
    identity=reserve(repair,repair.run_id(case,1,0),'.03');repair.settle(identity,estimate='.002',usage={})
    assert repair.validated_project_limit(repair.run_id(case,1,0))==Decimal('34')
    with pytest.raises(Exception,match='terminal_phase_no_retry'):reserve(parent,parent.run_id(case,1,0))
    assert parent.phase_outcome(parent.run_id(case,1,0))=='model_failure'


def test_changed_parent_charge_or_second_amendment_is_rejected(tmp_path):
    parent,p,path,grant=setup(tmp_path);PilotLedger(parent.path,path,grant)
    changed=dict(grant,grant_message='UNIT TEST different amendment identity')
    with pytest.raises(ValueError,match='One explicit repair'):PilotLedger(parent.path,path,changed)
    with sqlite3.connect(parent.path) as db:db.execute("UPDATE reservations SET estimated_cny='.02'")
    with pytest.raises(ValueError,match='known and unchanged'):PilotLedger(parent.path,path,grant)


def test_parent_and_repair_share_the_same_six_yuan_round_cap(tmp_path):
    parent,p,path,grant=setup(tmp_path);repair=PilotLedger(parent.path,path,grant)
    with sqlite3.connect(repair.path) as db:
        db.execute('INSERT INTO budget_holds VALUES (?,?,?,?)',('unit-hold',repair.prefix+'another:r1','5.98','Unit whole-round hold'))
    with pytest.raises(Exception,match='whole_round'):reserve(repair,repair.run_id(p['case_ids'][0],1,0),'.02')


def test_unknown_repair_charge_holds_full_case_and_does_not_resolve_or_retry(tmp_path):
    parent,p,path,grant=setup(tmp_path);repair=PilotLedger(parent.path,path,grant);run=repair.run_id(p['case_ids'][0],1,0)
    with pytest.raises(ValueError,match='Unknown fee'):repair.hold_uncertain_case(run)
    identity=reserve(repair,run,'.02');repair.settle(identity,usage=dict(error='unknown_execution_status'))
    assert repair.hold_uncertain_case(run)==Decimal('.48') and repair.hold_uncertain_case(run)==Decimal('.48')
    assert repair.summary(run)['case_committed_estimate_cny']=='0.50'
    assert repair.summary(run)['unknown_cost_requests']==1
    with pytest.raises(Exception):reserve(repair,run,'.02')
