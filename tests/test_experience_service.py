import json
import pytest
from fastapi.testclient import TestClient

from app.service.api import create_app
from app.service.runtime import Worker
from app.service.store import Conflict, digest
from tests.test_public_workbench import fixture_service


BODY = '频次检查需核对历史覆盖。velocity incomplete observation frequency：覆盖不完整且频次不足时应报告资料不足，不可用经验认定欺诈。'


def rejected(service, request='lesson-source-01'):
    identity, _ = service.submit(request, case_id='new-pattern', question={'checks':['frequency']}, task_type='mixed')
    row = Worker(service).step()
    assert row['state'] == 'awaiting_review'
    service.review(identity, request_key=request+'-review', expected_version=row['version'],
                   result_hash=row['result_hash'], decision='reject_report')
    return service.store.get(identity)


def proposal(service, row, body=BODY):
    return service.experience.propose(source_job=row['id'], result_hash=row['result_hash'],
        identity='coverage', body=body, feedback='人工反馈：应核对观察覆盖边界；仅作机制测试，不是真实模型错误。')


def test_api_complete_feedback_evaluation_promotion_and_frozen_job(fixture_service):
    service, _ = fixture_service
    row = rejected(service)
    before = json.loads(service.artifact(row['id'], 'knowledge.json').read_text())
    assert before['hits'] == []
    with TestClient(create_app(service), headers={'Authorization':'Bearer '+service.settings.token}) as api:
        assert api.get('/knowledge/search', params={'query':'频次覆盖'}).json()['hits'] == []
        payload = dict(source_job=row['id'], result_hash=row['result_hash'], identity='coverage', body=BODY, feedback='覆盖规则审阅')
        candidate = api.post('/knowledge/candidates', json=payload).json()
        assert api.post('/knowledge/candidates/'+candidate['id']+'/promote', json={'evaluation_hash':'0'*64}).status_code == 409
        evaluated = api.post('/knowledge/candidates/'+candidate['id']+'/evaluate', json={}).json()
        assert evaluated['evaluation']['passed']
        assert any(not r['before'] and r['after'] for r in evaluated['evaluation']['outcomes'] if r['partition']=='transfer')
        path = '/knowledge/candidates/'+candidate['id']+'/promote'
        promoted = api.post(path, json={'evaluation_hash':evaluated['evaluation_hash']})
        assert promoted.status_code == 200 and promoted.json()['promoted']
        assert api.post(path, json={'evaluation_hash':evaluated['evaluation_hash']}).status_code == 200
        assert api.get('/knowledge/search', params={'query':'velocity incomplete observation'}).json()['hits']
        assert json.loads(service.artifact(row['id'], 'knowledge.json').read_text()) == before
        identity, _ = service.submit('knowledge-after-01', case_id='new-pattern', question={'checks':['frequency']}, task_type='mixed')
        assert Worker(service).step()['state'] == 'awaiting_review'
        artifact = json.loads(service.artifact(identity, 'knowledge.json').read_text())
        assert artifact['hits'] and artifact['advisory_only']
        assert 'K:' not in service.artifact(identity, 'report.md').read_text()
        assert api.get('/').text.count('lessonPromote') >= 2


def test_candidate_cannot_use_unrejected_or_wrong_result(fixture_service):
    service, _ = fixture_service; row = rejected(service)
    with TestClient(create_app(service), headers={'Authorization':'Bearer '+service.settings.token}) as api:
        body = dict(source_job=row['id'], result_hash='0'*64, identity='coverage', body=BODY, feedback='audit')
        assert api.post('/knowledge/candidates', json=body).status_code == 409
        assert api.get('/knowledge/search', params={'query':'x'*301}).status_code == 422
        assert api.get('/knowledge/search', params={'query':'频次'}, headers={'Authorization':''}).status_code == 401


@pytest.mark.parametrize('body', ['无关说明', BODY+' weather tomorrow 天气预报'])
def test_no_gain_or_regression_blocks_promotion(fixture_service, body):
    service, _ = fixture_service; row = rejected(service)
    candidate = proposal(service, row, body)
    result = service.experience.evaluate(candidate['id'])
    assert not result['evaluation']['passed']
    with pytest.raises(Conflict): service.experience.promote(candidate['id'], evaluation_hash=result['evaluation_hash'])
    assert service.experience.corpus() == []


def test_concurrent_corpus_change_fences_stale_evaluation(fixture_service):
    service, _ = fixture_service; row = rejected(service)
    one, two = proposal(service, row), proposal(service, row)
    first, second = service.experience.evaluate(one['id']), service.experience.evaluate(two['id'])
    service.experience.promote(one['id'], evaluation_hash=first['evaluation_hash'])
    with pytest.raises(Conflict, match='stale'): service.experience.promote(two['id'], evaluation_hash=second['evaluation_hash'])


def test_submission_snapshot_survives_later_promotion(fixture_service):
    service, _ = fixture_service; row = rejected(service)
    identity, _ = service.submit('before-promotion-01', case_id='new-pattern', question={'checks':['frequency']}, task_type='mixed')
    candidate = proposal(service, row); evaluated = service.experience.evaluate(candidate['id'])
    service.experience.promote(candidate['id'], evaluation_hash=evaluated['evaluation_hash'])
    assert Worker(service).step()['state'] == 'awaiting_review'
    assert json.loads(service.artifact(identity, 'knowledge.json').read_text())['hits'] == []


def test_latest_only_snapshot_and_second_version_gate(fixture_service):
    from app.service.experience import SCOPE
    service, _ = fixture_service; row = rejected(service)
    service.experience.store.add_reviewed(scope=SCOPE, identity='coverage', version=1,
        available_at='2020-01-01T00:00:00+00:00', kind='lesson', body='旧版无关说明', source='fixture', reviewer='fixture')
    candidate = proposal(service, row)
    evaluated = service.experience.evaluate(candidate['id'])
    assert evaluated['evaluation']['passed'] and evaluated['evaluation']['version'] == 2
    service.experience.promote(candidate['id'], evaluation_hash=evaluated['evaluation_hash'])
    assert len(service.experience.corpus()) == 1 and service.experience.corpus()[0][1] == 2


def test_live_probe_keeps_invalid_output_without_real_requests(fixture_service, tmp_path, monkeypatch):
    from app.cli.experience_demo import live_probe
    service, _ = fixture_service
    class MockClient:
        def __init__(self, *args, **kwargs): pass
        def chat(self, messages, tools=None):
            return {'role':'assistant','content':'untrusted prose\n```json\n{"answer":null,"citations":[]}\n```'}
        def scrub(self, value): return value
        def snapshot(self): return {'requests_sent':0, 'simulation':True}
        def close(self): pass
    monkeypatch.setattr('app.cli.investigate_live.local_key', lambda:'MOCK-NO-KEY')
    monkeypatch.setattr('app.llm.siliconflow_client.SiliconFlowClient', MockClient)
    results = live_probe(service, tmp_path)
    assert len(results) == 8 and all(r['status']=='failed' for r in results)
    assert all(r['raw_answer'].startswith('untrusted prose') and r['messages'] for r in results)
    assert all(r['usage']['requests_sent']==0 for r in results)
