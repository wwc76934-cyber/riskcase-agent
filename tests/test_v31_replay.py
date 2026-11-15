from copy import deepcopy
import hashlib,json,sqlite3
import pytest
from tests.test_fraud_inquiry import inquiry_snapshot
from tests.test_v3_reliability import acquired
from app.evaluation.v31_saved_response_audit import full_audit,report_conversion

def test_final_conversion_does_not_invent_new_business_disposition():
    old=dict(investigation=dict(fact_ids=['F1']),source_statement=dict(transaction_id='P',value='unresolved'))
    before=deepcopy(old);new=report_conversion(old)
    assert old==before and 'proposed_disposition' not in new
    assert 'transaction_id' not in new['source_statement'] and 'fact_ids' not in new['investigation']

def test_full_readonly_replay_preserves_raw_database_and_denominators(tmp_path,acquired):
    from app.investigation.v3.output import assemble,extract
    ledger,evidence,facts,decision=acquired
    pilot=tmp_path/'pilot';(pilot/'live').mkdir(parents=True);dataset=tmp_path/'dataset';dataset.mkdir()
    def write(path,value):path.write_text(json.dumps(value),encoding='utf-8')
    packet=dict(case_id=ledger.case_id,cutoff=ledger.cutoff,as_of=ledger.as_of,subject_id=ledger.subject_id,
        transaction_ids=sorted(ledger.transaction_ids),target_id=ledger.target_id,evidence=evidence)
    write(pilot/'packet.json',packet);write(pilot/'prepared.json',dict(jobs=[dict(key='case-T1',case_id=ledger.case_id,stage=1,source_packet='packet.json')]))
    reference=dict(stages=[{},dict(expected_observations=[],expected_source_statement=dict(value='unresolved'))]);write(dataset/'reference.json',reference)
    write(dataset/'dataset.json',dict(entries=[dict(case_id=ledger.case_id,reference='reference.json',reference_sha256=hashlib.sha256((dataset/'reference.json').read_bytes()).hexdigest())]))
    report=dict(investigation=decision,source_statement=dict(transaction_id=ledger.target_id,value='unresolved',fact_ids=[],rationale='Unit not model evidence'))
    dbpath=pilot/'live/jobs.sqlite'
    with sqlite3.connect(dbpath) as db:
        db.execute('CREATE TABLE jobs(id TEXT,request_key TEXT)');db.execute('INSERT INTO jobs VALUES (?,?)',('job','case-T1'))
        db.execute('CREATE TABLE calls(job_id TEXT,ordinal INTEGER,response TEXT,state TEXT)')
        for ordinal,payload in [(1,facts),(2,report)]:db.execute('INSERT INTO calls VALUES (?,?,?,?)',('job',ordinal,json.dumps(dict(raw_response=dict(content=json.dumps(payload),role='assistant'))),'completed'))
    fingerprint=hashlib.sha256(dbpath.read_bytes()).hexdigest();result=full_audit(pilot,dataset,tmp_path/'result.json')
    assert result['planned_stages']==result['recorded_stages']==1 and result['report_responses']==1
    assert result['original_deliveries_replayed']==1 and result['original_target_correct_T1_T2_reproduced']==1
    assert result['new_final_reports_compatible']==0 and result['external_model_requests']==0
    assert fingerprint==hashlib.sha256(dbpath.read_bytes()).hexdigest()
    with pytest.raises(FileExistsError):full_audit(pilot,dataset,tmp_path/'result.json')
