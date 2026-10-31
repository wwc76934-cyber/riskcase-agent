from copy import deepcopy
import json
from pathlib import Path
import shutil
import pytest

from app.investigation.fraud import BoundSource,from_case,verify,FraudScriptedClient,digest
from app.investigation.fraud_service import FraudService
from app.investigation.harness import HarnessWorker
from app.investigation.tickets import Tickets,MockDestination
from app.cli.fraud_bridge import queue_audit
from app.service.store import Conflict
from tests.test_merchant_research import manifest


@pytest.fixture
def fraud_case(tmp_path,manifest):
    folder=tmp_path/'input';folder.mkdir()
    shutil.copyfile(manifest.parent/'facts.sqlite',folder/'facts.sqlite')
    value=json.loads(manifest.read_text())
    value['alert'].update(day_rank=1,detector='neural_v2_ensemble',
        score_kind='uncalibrated_ranking_sigmoid_not_probability',model_artifacts_sha256='a'*64,
        model_report_sha256='b'*64,selection='unit_test_fixture_NOT_detector_evaluation')
    path=folder/'manifest.json';path.write_text(json.dumps(value))
    return path,from_case(path,'fixture')


def record(source,calls):
    evidence={}
    for name,args in calls:
        result=source.call(name,args);evidence['E'+str(len(evidence)+1)]=dict(result=result,tool=name,arguments=args,sha256=digest(result))
    return evidence


def answer_for(evidence):
    observations=[dict(ok=True,evidence_id=k,**v['result']) for k,v in evidence.items()]
    observations.insert(0,dict(kind='skill'))
    return json.loads(FraudScriptedClient().chat([{},dict(content=json.dumps({'observations':observations}))])['content'])


def facts(snapshot):
    return record(BoundSource(snapshot),[('fraud_alert',{}),('fraud_policy',{}),
        ('fraud_history',{'lookback_hours':1}),('fraud_history',{'lookback_hours':720}),('fraud_merchant',{'lookback_hours':720})])


@pytest.mark.parametrize('change',['label','manifest_label','future','other_user','wrong_alert','nan_score','duplicate'])
def test_source_rejects_invalid_boundary(fraud_case,change):
    _,s=fraud_case;s=deepcopy(s)
    if change=='label':s['rows'][0]['isFraud']=1
    if change=='manifest_label':s['manifest']['gold']='fraud'
    if change=='future':s['rows'][0]['event_time']='2018-12-31T00:00:00'
    if change=='other_user':s['rows'][0]['user_id']='someone_else'
    if change=='wrong_alert':s['manifest']['alert']['transaction_id']='another'
    if change=='nan_score':s['manifest']['alert']['score']=float('nan')
    if change=='duplicate':s['rows'][0]['transaction_id']=s['rows'][1]['transaction_id']
    with pytest.raises(ValueError):BoundSource(s)


def test_findings_not_fraud_or_dispute_conclusions(fraud_case):
    _,s=fraud_case;evidence=facts(s);answer=answer_for(evidence);result=verify(answer,evidence)
    assert result['fraud_determination'] is None and result['semantic_validation'] is False
    assert answer['verdict']=='analyst_review' and 'confirmed_case_outcome' in answer['missing']
    changed=deepcopy(answer);changed['findings'][0]['assessment']='not_supported'
    with pytest.raises(ValueError):verify(changed,evidence)
    changed=deepcopy(evidence);changed['E3']['result']['data']['prior_count']=100
    with pytest.raises(ValueError):verify(answer,changed)
    from jsonschema import ValidationError
    for verdict in ('normal_change','close_explained','confirmed_fraud','safe'):
        with pytest.raises(ValidationError):verify(dict(answer,verdict=verdict),evidence)


def test_all_negative_leads_do_not_clear_alert(fraud_case):
    _,s=fraud_case;s=deepcopy(s)
    target=next(r for r in s['rows'] if r['transaction_id']=='target')
    target.update(amount_cents=50,merchant_id='B')
    e=facts(s);a=answer_for(e)
    assert all(f['assessment']=='not_supported' for f in a['findings'])
    assert verify(a,e)['fraud_determination'] is None and a['verdict']=='analyst_review'


def test_incomplete_source_requires_records(fraud_case):
    _,s=fraud_case;s=deepcopy(s);s['manifest']['full_source_scanned']=False
    e=facts(s);a=answer_for(e);assert a['verdict']=='request_records'
    verify(a,e)
    with pytest.raises(ValueError):verify(dict(a,verdict='analyst_review'),e)


def test_real_mcp_harness_and_explicit_review(tmp_path,fraud_case):
    _,s=fraud_case;service=FraudService(tmp_path/'service')
    job,_=service.submit('fraud-test-0001',s,'Check the three leads')
    row=HarnessWorker(service).step()
    assert row['state']=='awaiting_review',row['reason']
    assert row['result']['status']=='fraud_leads_awaiting_review'
    assert row['spec']['knowledge']['documents']==[] and row['spec']['knowledge']['memory']['lesson'] is None
    assert row['result']['active_skills'].keys()=={'fraud-alert-review'}
    handshakes=json.loads((service.directory/job/'mcp-handshakes.json').read_text())
    assert set(handshakes)=={'transactions','documents'}
    assert service.submit('fraud-test-0001',s,'Check the three leads')==(job,False)
    tickets=Tickets(service.store,MockDestination(service.directory/'mock.sqlite'))
    draft=tickets.draft(job,'Human review of behavior and missing authorization required')
    with pytest.raises(Conflict):tickets.approve(job,draft['hash'])
    with pytest.raises(Conflict):tickets.approve(job,draft['hash'],reviewed_claims=['settled_debits','policy','missing'])
    tickets.approve(job,draft['hash'],reviewed_claims=['findings','scope','missing'])
    assert tickets.submit(job,draft['hash'])['status']=='submitted'
    with pytest.raises(Conflict,match='not_applicable'):service.experiences.candidate(job,['coverage','settlement','merchant'])


def test_capacity_audit_counts_transactions_without_fake_time_savings():
    rows=[dict(transaction_id=str(i),user_id='u',event_time='2018-01-01T12:00:00',score=.9-i*.1,day_rank=i+1,tie_hash=str(i)) for i in range(3)]
    report=queue_audit(rows)
    assert report['repeated_user_day_slots']==2 and report['human_minutes_saved'] is None and not report['grouping_applied']
    with pytest.raises(ValueError):queue_audit(rows+[rows[0]])
    with pytest.raises(ValueError):queue_audit([dict(rows[0],isFraud=1)])
    with pytest.raises(ValueError):queue_audit([dict(rows[0],day_rank=2)])


def test_fraud_response_journal_resumes_without_reissuing_saved_response(tmp_path,fraud_case):
    _,snapshot=fraud_case;service=FraudService(tmp_path/'resume')
    job,_=service.submit('fraud-resume-0001',snapshot,'Check three leads')
    class Crash(BaseException):pass
    def hook(point,ordinal):
        if point=='response_saved' and ordinal==2:raise Crash()
    with pytest.raises((Crash,BaseExceptionGroup)) as stopped:HarnessWorker(service,hook=hook).step()
    cause=stopped.value
    while isinstance(cause,BaseExceptionGroup):
        assert len(cause.exceptions)==1
        cause=cause.exceptions[0]
    assert isinstance(cause,Crash)  # MCP/AnyIO wraps the injected process-like stop.
    with service.store.transaction() as db:db.execute('UPDATE jobs SET lease_until=0 WHERE id=?',(job,))
    row=HarnessWorker(service).step()
    assert row['state']=='awaiting_review' and row['generation']==2
    assert row['result']['replayed_client_responses']==1
    assert row['result']['validation']['fraud_determination'] is None


def test_shared_fact_code_is_part_of_frozen_execution_fingerprint(monkeypatch):
    from app.investigation.harness import fingerprint
    before=fingerprint();original=Path.read_text
    def changed(path,*args,**kwargs):
        text=original(path,*args,**kwargs)
        return text+'\n# changed calculation' if path.as_posix().endswith('app/tools/case_tools.py') else text
    monkeypatch.setattr(Path,'read_text',changed)
    assert fingerprint()!=before


def test_api_accepts_registered_alert_only(tmp_path,fraud_case):
    from fastapi.testclient import TestClient
    from app.service.api import create_app
    from app.service.runtime import ResearchService,Settings
    path,_=fraud_case
    parent=ResearchService(Settings(Path(__file__).resolve().parents[1],tmp_path/'parent',{'fixture':path},'t'*40))
    with TestClient(create_app(parent),headers={'Authorization':'Bearer '+'t'*40}) as client:
        rows=client.get('/fraud-alerts').json()['alerts'];assert rows[0]['case_id']=='fixture'
        data=dict(case_id='fixture',text='Review behavior')
        response=client.post('/fraud-investigations',json=data,headers={'Idempotency-Key':'fraud-api-0001'})
        assert response.status_code==200
        assert client.post('/fraud-investigations',json=dict(data,case_id='../other'),headers={'Idempotency-Key':'fraud-api-0002'}).status_code>=400
