from copy import deepcopy
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.risk.mixed_investigation import CHECKS, MixedQuestion, MixedTools, ScriptedMixedClient, check_answer, expected_answer, finding, preload, query_plan, run_fixed, run_mixed, validate_answer
from app.risk.mixed_sql import SourceAudit
from app.cli.mixed_investigation import execute
from app.llm.fake_client import FakeLLMClient
from app.llm.fake_client import make_tool_call_message
from app.service.api import create_app
from app.service.runtime import ResearchService, Settings, Worker
from scripts.verify_mixed import verify
from tests.test_merchant_research import manifest


@pytest.mark.parametrize("checks,count", [(('frequency',), 2), (('amount','merchant'), 3), (('count_share','amount_share'), 3), (CHECKS, 6)])
def test_question_aware_plan_and_three_offline_arms(manifest, checks, count):
    question = MixedQuestion(checks, 1)
    assert len(query_plan(question)) == count
    fixed = run_fixed(MixedTools(manifest), question)
    tools = MixedTools(manifest)
    agent = run_mixed(ScriptedMixedClient(question), tools, question, client_mode="SCRIPTED_NOT_LLM")
    preloaded = MixedTools(manifest); preload(preloaded, question)
    summary = run_mixed(ScriptedMixedClient(question, preloaded.evidence), preloaded, question, client_mode="SCRIPTED_NOT_LLM", preloaded=True)
    assert fixed["findings"] == agent["findings"] == summary["findings"]
    assert fixed["tool_attempts"] == agent["tool_attempts"] == summary["tool_attempts"] == count
    assert agent["status"] == summary["status"] == "completed_validated"
    assert agent["client_requests"] <= 4 and fixed["client_requests"] == 0
    assert summary["client_requests"] == 1


@pytest.mark.parametrize("kwargs", [{"checks": []}, {"checks": ['amount','amount']}, {"checks": ['freeze']}, {"checks": 'amount'}, {"checks": [True]}, {"min_records": True}, {"min_records": 0}])
def test_invalid_contract(kwargs):
    with pytest.raises(ValueError): MixedQuestion(**kwargs)


def test_no_subset_success_and_unsolicited_checks(manifest):
    tools = MixedTools(manifest); question = MixedQuestion(min_records=1); preload(tools, question)
    valid = expected_answer(tools.evidence, question)
    for changed in [dict(findings=valid['findings'][:-1]), dict(findings=valid['findings']+[valid['findings'][0]])]:
        assert check_answer(json.dumps(changed), tools.evidence, question)[1] == "answer_contract_or_evidence"
    changed = deepcopy(valid); changed['findings'][0]['evidence_ids'] = ['invented']
    with pytest.raises(ValueError): validate_answer(json.dumps(changed), tools.evidence, question)
    changed = deepcopy(valid); changed['findings'][0]['assessment'] = 'supported'
    with pytest.raises(ValueError): validate_answer(json.dumps(changed), tools.evidence, question)
    assert check_answer('Explanation '+json.dumps(valid), tools.evidence, question)[1] == 'answer_format'
    assert check_answer('```json\n'+json.dumps(valid)+'\n```', tools.evidence, question)[1] is None


def test_incomplete_observed_matches_are_counterevidence(manifest):
    tools = MixedTools(manifest); q = MixedQuestion(min_records=1); preload(tools, q)
    registry = deepcopy(tools.evidence)
    for row in registry.values():
        if row['tool'] != 'get_alert_transaction': row['complete_within_source'] = False
    assert finding('merchant', registry, q)['assessment'] == 'insufficient_evidence'  # Target Z has no match.
    for row in registry.values():
        if row['tool'] == 'query_target_merchant': row['merchant_prior_count'] = 1
        if row['tool'] == 'summarize_history' and row['lookback_hours'] == 1: row['prior_count'] = 4
    assert finding('merchant', registry, q)['assessment'] == 'not_supported'
    assert finding('frequency', registry, q)['assessment'] == 'supported'
    assert finding('amount', registry, q)['assessment'] == 'insufficient_evidence'
    assert finding('count_share', registry, q)['assessment'] == 'insufficient_evidence'
    assert finding('amount_share', registry, q)['assessment'] == 'insufficient_evidence'


def test_amount_positive_sample_and_complete_coverage_are_separate(manifest):
    tools = MixedTools(manifest); q = MixedQuestion(min_records=5); preload(tools, q)
    body = expected_answer(tools.evidence, q)
    values = {r['check']: r['assessment'] for r in body['findings']}
    assert values['count_share'] == 'decreased' and values['amount_share'] == 'insufficient_evidence'


def test_one_format_repair_and_no_semantic_retry(manifest):
    q = MixedQuestion(('frequency',), 1); tools = MixedTools(manifest); preload(tools, q)
    good = expected_answer(tools.evidence, q)
    class Client:
        def __init__(self, first): self.first, self.calls = first, 0
        def chat(self, messages, tools=None):
            self.calls += 1
            return {'role':'assistant','content':self.first if self.calls == 1 else json.dumps(good)}
    client = Client('Here is '+json.dumps(good))
    result = run_mixed(client, tools, q, client_mode='TEST', preloaded=True)
    assert result['status'] == 'completed_validated' and result['format_repair']['attempted'] and client.calls == 2
    bad = deepcopy(good); bad['findings'][0]['assessment'] = 'supported'
    client = Client(json.dumps(bad))
    result = run_mixed(client, tools, q, client_mode='TEST', preloaded=True)
    assert result['status'] == 'answer_rejected' and client.calls == 1 and not result['format_repair']['attempted']


def test_all_tool_data_scoped_and_cache_safe(manifest):
    tools = MixedTools(manifest); q = MixedQuestion(min_records=1); preload(tools, q)
    text = json.dumps(tools.evidence)
    assert all(word not in text for word in ('same-time','future','other-user','is_fraud'))
    assert tools.attempts == 6
    assert not tools.dispatch('get_alert_transaction','{}')['ok']
    tools = MixedTools(manifest)
    first = tools.dispatch('summarize_history','{"lookback_hours":1}')
    first['data']['prior_count'] = 999
    assert tools.dispatch('summarize_history','{"lookback_hours":1}')['data']['prior_count'] != 999
    assert tools.trace[-1]['cache_hit']
    assert not tools.dispatch('summarize_history','{"lookback_hours":true}')['ok']


@pytest.mark.parametrize('arm', ['fixed','evidence_only','agent'])
def test_sql_verifies_each_arm_and_rejects_tamper(manifest, tmp_path, arm):
    root = Path(__file__).resolve().parents[1]; out = tmp_path / arm
    summary = execute(root, out, manifest, MixedQuestion(min_records=1), arm=arm)
    with SourceAudit(manifest.parent.parent / 'build-report.json') as audit:
        proof = verify(out / 'summary.json', audit)
        assert proof['task_passed'] and not proof['labels_opened'] and proof['missing_required_queries'] == 0
        assert proof['gold'] == {r['check']:r['assessment'] for r in summary['findings']['findings']}
        (out / 'trace.json').write_text('{}')
        with pytest.raises(ValueError, match='Artifact changed'): verify(out / 'summary.json', audit)


def test_mixed_task_through_persistent_service(manifest, tmp_path):
    root = Path(__file__).resolve().parents[1]
    service = ResearchService(Settings(root, tmp_path / 'service', {'fixture':manifest}, 'x'*40))
    with TestClient(create_app(service), headers={'Authorization':'Bearer '+'x'*40}) as client:
        body = {'case_id':'fixture','task_type':'mixed','question':{'checks':['amount','merchant'],'min_records':1}}
        submitted = client.post('/jobs',json=body,headers={'Idempotency-Key':'mixed-job-0001'})
        assert submitted.status_code == 202
        identity = submitted.json()['id']; row = Worker(service).step()
        assert row['state'] == 'awaiting_review' and row['result']['new_client_requests'] == 3
        assert client.get(f'/jobs/{identity}/artifacts/summary.json').json()['task_type'] == 'mixed'
        assert '混合预警调查' in client.get(f'/jobs/{identity}/artifacts/report.md').text
        with SourceAudit(manifest.parent.parent/'build-report.json') as audit:
            assert verify(service.artifact(identity,'summary.json'),audit)['task_passed']
        invalid = {'case_id':'fixture','task_type':'mixed','question':{'short_hours':168}}
        assert client.post('/jobs',json=invalid,headers={'Idempotency-Key':'mixed-job-0002'}).status_code == 422


def test_model_never_receives_gold_or_label_database(manifest, monkeypatch):
    import sqlite3
    original = sqlite3.connect
    def guarded(path, *args, **kwargs):
        assert 'label' not in str(path).lower()
        return original(path, *args, **kwargs)
    monkeypatch.setattr(sqlite3,'connect',guarded)
    tools = MixedTools(manifest); q = MixedQuestion(min_records=1)
    class Audited(ScriptedMixedClient):
        def chat(self, messages, tools=None):
            text = json.dumps(messages)
            assert '"gold"' not in text and 'is_fraud' not in text and 'evaluation-labels' not in text
            return super().chat(messages, tools)
    assert run_mixed(Audited(q), tools, q, client_mode='TEST')['status'] == 'completed_validated'


def test_failed_semantic_answer_is_audited_without_publishing(manifest, tmp_path, monkeypatch):
    q = MixedQuestion(('frequency',),1)
    class Wrong(ScriptedMixedClient):
        def chat(self, messages, tools=None):
            answer = super().chat(messages, tools)
            if not answer.get('tool_calls'):
                body = json.loads(answer['content']); body['findings'][0]['assessment'] = 'supported'
                answer['content'] = json.dumps(body)
            return answer
    monkeypatch.setattr('app.cli.mixed_investigation.ScriptedMixedClient', Wrong)
    root = Path(__file__).resolve().parents[1]; out = tmp_path/'failed'
    summary = execute(root,out,manifest,q,arm='agent')
    assert summary['status'] == 'answer_rejected' and not (out/'report.md').exists()
    with SourceAudit(manifest.parent.parent/'build-report.json') as audit:
        proof = verify(out/'summary.json',audit)
        assert not proof['task_passed'] and not proof['answer_published']


def test_mixed_persistent_resume_replays_calls(manifest, tmp_path):
    root = Path(__file__).resolve().parents[1]
    service = ResearchService(Settings(root,tmp_path/'resume',{'fixture':manifest},'m'*40))
    identity,_ = service.submit('mixed-resume-0001',case_id='fixture',question={'checks':list(CHECKS),'min_records':1},task_type='mixed')
    now=[1000.]; service.store.clock=lambda:now[0]
    class Crash(BaseException): pass
    def stop(n):
        if n==2: raise Crash()
    with pytest.raises(Crash): Worker(service,after_saved=stop).step()
    now[0]+=91
    row=Worker(service).step()
    assert row['id']==identity and row['state']=='awaiting_review' and row['generation']==2
    assert row['result']['replayed_client_responses']==2 and row['result']['new_client_requests']==2


class OverusingMixedClient(ScriptedMixedClient):
    def chat(self, messages, tools=None):
        response=super().chat(messages,tools)
        if len(response.get('tool_calls',[]))==2:
            response['tool_calls'].append(make_tool_call_message('extra-target','get_alert_transaction',{})['tool_calls'][0])
        return response


def test_numerically_correct_overbudget_answer_is_not_publishable(manifest):
    q=MixedQuestion(min_records=1);tools=MixedTools(manifest)
    result=run_mixed(OverusingMixedClient(q),tools,q,client_mode='OFFLINE_OVERUSE_FIXTURE')
    assert tools.attempts==7 and sum(row['result']['ok'] for row in tools.trace)==6
    assert result['status']=='tool_budget_exceeded' and result['failure_stage']=='tool_budget'
    assert result['findings'] is None and result['report_markdown'] is None


def test_service_cannot_publish_overbudget_report(manifest,tmp_path):
    root=Path(__file__).resolve().parents[1]
    service=ResearchService(Settings(root,tmp_path/'overbudget',{'fixture':manifest},'b'*40))
    identity,_=service.submit('overbudget-service-0001',case_id='fixture',question={'checks':list(CHECKS),'min_records':1},task_type='mixed')
    row=Worker(service,client_factory=OverusingMixedClient).step()
    assert row['id']==identity and row['state']=='failed' and row['reason']=='tool_budget_exceeded'
    directory=service.settings.directory/row['result']['directory']
    assert not (directory/'report.md').exists()
