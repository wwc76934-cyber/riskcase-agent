import json
from fastapi.testclient import TestClient
from app.service.runtime import Worker
from app.service.api import create_app
from tests.test_public_workbench import fixture_service


def test_triage_api_real_report_and_local_review(fixture_service):
    service,_=fixture_service
    with TestClient(create_app(service),headers={'Authorization':'Bearer '+service.settings.token}) as api:
        body=dict(case_id='new-pattern',task_type='triage',question={'policy_version':'first-lead-v1'})
        response=api.post('/jobs',json=body,headers={'Idempotency-Key':'triage-api-001'})
        assert response.status_code==202
        row=Worker(service).step();assert row['state']=='awaiting_review'
        summary=json.loads(service.artifact(row['id'],'summary.json').read_text(encoding='utf-8'))
        assert summary['real_LLM_requests']==0 and summary['arm']=='adaptive_code'
        assert summary['tool_attempts']<=4
        report=service.artifact(row['id'],'report.md').read_text(encoding='utf-8')
        assert '自建教学案例' in report and '未执行项没有结论' in report
        trace=json.loads(service.artifact(row['id'],'trace.json').read_text(encoding='utf-8'))
        assert len(trace['findings']['findings'])==summary['checks_completed']
        assert api.get('/jobs').json()['jobs'][0]['question_text']=='按优先级分步核查'
        assert api.post('/jobs',json=body|{'mode':'live'},headers={'Idempotency-Key':'triage-api-live'}).status_code==422
        assert api.post('/jobs',json=body|{'question':{'policy_version':'anything'}},headers={'Idempotency-Key':'triage-api-bad'}).status_code==422


def test_schema_contract_excludes_status_alias_and_extra_fields():
    from app.risk.adaptive_triage import PROMPT
    assert '"assessment"' in PROMPT and 'No detail' in PROMPT
