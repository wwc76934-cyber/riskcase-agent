import json,hashlib,sqlite3
import pytest
from tests.test_v3_pilot_ledger import fixture
from tests.test_v3_typed_repair import prepared
from app.evaluation.v3_pilot_ledger import PilotLedger
from app.evaluation.v3_typed_repair import execute,save,sha
from app.evaluation.v34_selection_trial import prepare

def test_selection_profile_cannot_borrow_schema_profile_authority(tmp_path):
    path,p,g=fixture(tmp_path)
    p.update(schema='v34-selected-fact-development-v1',transport_contract='v33-strict-json-schema-v1',
        delivery_contract='v31-selected-customer-fact-report-v1',credential_preflight='v31-negative-control-auth-v1',
        max_estimated_cny='3',max_input_bytes=24000,max_request_reservation_cny='0.034304')
    path=tmp_path/'selection.json';path.write_text(json.dumps(p),encoding='utf-8')
    with pytest.raises(ValueError):PilotLedger(tmp_path/'bad.sqlite',path,g)
    g.update(max_estimated_cny='3',proposal_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    PilotLedger(tmp_path/'ok.sqlite',path,g)
    p['schema']='v33-schema-development-v1';path.write_text(json.dumps(p),encoding='utf-8')
    g['proposal_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError):PilotLedger(tmp_path/'bad.sqlite',path,g)
    assert not (tmp_path/'bad.sqlite').exists()

def test_failed_read_only_preflight_prevents_all_generation_and_preserves_diagnostics(tmp_path,monkeypatch):
    _,book=prepared(tmp_path);out=tmp_path/'selection';p=prepare(tmp_path/'parent',tmp_path/'data',out,book)
    save(out/'authorization.json',dict(state='approved',grant_message='UNIT ONLY not user consent',proposal_sha256=sha(out/'proposal.json'),max_estimated_cny='3',project_limit_cny='34'))
    from app.cli import investigate_live
    from app.investigation.v3 import auth_preflight
    monkeypatch.setattr(investigate_live,'local_key',lambda:'sk-unit-preflight-key')
    seen=[]
    def probe(key):
        seen.append(key)
        return dict(permit_separately_authorized_attempt=False,verdict='credential_rejected',generation_requests=0)
    monkeypatch.setattr(auth_preflight,'probe_project_credential',probe)
    with sqlite3.connect(book) as db:before=db.execute('SELECT COUNT(*) FROM reservations').fetchone()[0]
    with pytest.raises(ValueError,match='no generation request'):execute(out,live=True)
    with sqlite3.connect(book) as db:assert db.execute('SELECT COUNT(*) FROM reservations').fetchone()[0]==before
    assert seen==['sk-unit-preflight-key'] and not (out/'live').exists()
    assert json.loads((out/'auth-preflight-001.json').read_text(encoding='utf-8'))['generation_requests']==0
