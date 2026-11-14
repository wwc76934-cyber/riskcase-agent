import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from tests.test_fraud_inquiry import inquiry_snapshot
from app.evaluation.fraud_world_data import make_case
from app.service.runtime import ResearchService,Settings,Worker
from app.service.world_catalog import configure
from app.service.api import create_app
from app.risk.history import sha256_file


def test_registered_world_control_citations_review_and_no_paid_api(tmp_path,inquiry_snapshot):
    runtime,_=make_case(inquiry_snapshot,'auth_denial',1,'world-ui-fixture')
    path=tmp_path/'runtime.json';path.write_text(json.dumps(runtime),encoding='utf-8')
    catalog=tmp_path/'catalog.json';catalog.write_text(json.dumps(dict(contract='fraud-world-catalog-v1',origin=runtime['source_origin'],
        entries={runtime['case_id']:dict(path=str(path),sha256=sha256_file(path))})),encoding='utf-8')
    parent=ResearchService(Settings(Path(__file__).resolve().parents[1],tmp_path/'service',{},'t'*40));configure(parent,catalog)
    with TestClient(create_app(parent)) as api:
        assert '多来源欺诈调查' in api.get('/').text
        assert api.get('/world-cases').status_code==401
        api.headers.update({'Authorization':'Bearer '+'t'*40,'Idempotency-Key':'world-ui-request'})
        assert len(api.get('/world-cases').json()['cases'])==1
        body=dict(case_id=runtime['case_id'],text='核查授权、来源张力和逐笔时间线')
        assert api.post('/world-investigations',json=body|dict(mode='live')).status_code==422
        first=api.post('/world-investigations',json=body).json();identity=first['id']
        assert first['mode']=='offline_code_control_NOT_LLM'
        assert api.post('/world-investigations',json=body).json()['id']==identity
        row=Worker(parent).step();assert row['state']=='awaiting_review',row['reason']
        detail=api.get('/investigations/'+identity).json()
        assert detail['result']['client_kind']=='simulation' and detail['task_contract']=='fraud-world-v1'
        assert detail['investigation_scope']['investigation_as_of']==runtime['investigation_as_of']
        assert 'expected_claims' not in json.dumps(detail)
        draft=api.post('/investigations/'+identity+'/draft',json=dict(text='人工核查原件、假设和逐笔授权边界')).json()
        assert api.post('/investigations/'+identity+'/approve',json=dict(expected_hash=draft['hash'])).status_code==409
        assert api.post('/investigations/'+identity+'/approve',json=dict(expected_hash=draft['hash'],reviewed_claims=['observations','hypotheses','timeline','recommendations','missing'])).status_code==200
        assert api.get('/world-methods').json()['records']==[]
        path.write_text('{}')
        assert api.get('/world-cases').status_code==422
