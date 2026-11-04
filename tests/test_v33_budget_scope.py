import json,hashlib
from decimal import Decimal
import pytest
from tests.test_v3_pilot_ledger import fixture
from app.evaluation.v3_pilot_ledger import PilotLedger

def test_new_schema_profile_cannot_use_prior_grants_or_larger_request_envelopes(tmp_path):
    path,p,g=fixture(tmp_path)
    assert PilotLedger(tmp_path/'legacy.sqlite',path,g).batch_limit==Decimal('6')
    p.update(schema='v33-schema-development-v1',transport_contract='v33-strict-json-schema-v1',
        delivery_contract='v32-coded-report-plan-v1',max_estimated_cny='3',max_input_bytes=24000,max_request_reservation_cny='0.034304')
    path=tmp_path/'schema.json';path.write_text(json.dumps(p),encoding='utf-8')
    with pytest.raises(ValueError):PilotLedger(tmp_path/'bad.sqlite',path,g)
    g.update(max_estimated_cny='3',proposal_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    assert PilotLedger(tmp_path/'new.sqlite',path,g).batch_limit==Decimal('3')
    p['max_input_bytes']=24001;path.write_text(json.dumps(p),encoding='utf-8')
    g['proposal_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError,match='schema transport profile'):PilotLedger(tmp_path/'bad.sqlite',path,g)
    assert not (tmp_path/'bad.sqlite').exists()
