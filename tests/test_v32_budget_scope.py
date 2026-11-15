import hashlib,json,sqlite3
from decimal import Decimal
import pytest
from tests.test_v3_pilot_ledger import fixture,reserve
from app.evaluation.v3_pilot_ledger import PilotLedger
from app.llm.cost_ledger import BudgetExceeded


def test_coded_plan_requires_new_three_yuan_grant_and_keeps_legacy_six_unchanged(tmp_path):
    path,p,g=fixture(tmp_path)
    old=PilotLedger(tmp_path/'old.sqlite',path,g)
    assert old.batch_limit==Decimal('6')
    p.update(schema='v32-report-plan-development-v1',delivery_contract='v32-coded-report-plan-v1',
        max_estimated_cny='3',max_input_bytes=20000,max_request_reservation_cny='0.031104')
    path=tmp_path/'new.json';path.write_text(json.dumps(p),encoding='utf-8')
    # A prior grant or its higher amount is not authority for this new scope.
    with pytest.raises(ValueError):PilotLedger(tmp_path/'missing.sqlite',path,g)
    g['proposal_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError,match='3/34'):PilotLedger(tmp_path/'missing.sqlite',path,g)
    assert not (tmp_path/'missing.sqlite').exists()
    g['max_estimated_cny']='3';new=PilotLedger(tmp_path/'new.sqlite',path,g)
    assert new.batch_limit==Decimal('3') and new.project_limit==Decimal('34')
    with sqlite3.connect(new.path) as db:
        db.execute('INSERT INTO budget_holds VALUES (?,?,?,?)',('prior',new.prefix+'case:r1','2.99','UNIT protection only'))
    with pytest.raises(BudgetExceeded,match='round_limit'):
        reserve(new,new.run_id(p['case_ids'][0],1,0),'.02')
