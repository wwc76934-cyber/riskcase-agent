"""Mock HTTP and offline probes only. Never reads a real key or project ledger."""
import json
import hashlib
import sqlite3
from pathlib import Path
import httpx
import pytest
from app.evaluation.v3_tasks import build
from app.evaluation.v3_delivery_pilot import prepare,probe,freeze,execute,OfflineDeliveryProbe
from app.llm.cost_ledger import CostLedger
from app.llm.siliconflow_client import SiliconFlowClient,WORLD_MODEL


@pytest.fixture
def pilot(tmp_path_factory):
    root=tmp_path_factory.mktemp('v3-pilot');dataset=root/'dataset';directory=root/'pilot'
    build(dataset);proposal=prepare(dataset,directory)
    path=root/'TEST-ONLY-project-ledger.sqlite';CostLedger(path)
    with sqlite3.connect(path) as db:
        db.execute('INSERT INTO budget_holds VALUES (?,?,?,?)',('unit-prior','unit-legacy',proposal['project_before_pilot_committed_cny'],'UNIT TEST prior balance, not actual spend'))
    freeze(directory,dataset,project_ledger_path=path)
    return root,directory,proposal


def test_no_new_grant_means_no_credential_lookup_or_call(pilot,monkeypatch):
    _,directory,_=pilot
    def forbidden():raise AssertionError('A missing grant must stop before key lookup')
    monkeypatch.setattr('app.cli.investigate_live.local_key',forbidden)
    with pytest.raises(ValueError,match='Explicit live'):execute(directory)
    with pytest.raises(FileNotFoundError):execute(directory,live=True)


def test_preflight_is_not_a_model_result_and_packet_contains_no_reference(pilot):
    root,directory,p=pilot;r=probe(directory,root/'offline-probe')
    assert r['probe_phase_jobs']==36 and r['probe_requests']==72 and r['external_model_requests']==r['model_phase_jobs']==0
    assert not r['business_quality_scored'] and p['state']=='pending_user_authorization'
    assert float(p['all_known_usage_reservation_upper_cny'])<6 and p['largest_preflight_context_bound_bytes']<60000
    for path in (directory/'source-packets').glob('*.json'):
        packet=json.loads(path.read_text(encoding='utf-8'))
        assert not {'reference','family','fold','expected_source_statement'}&packet.keys()
        assert packet['evidence']['E2']['result']['data']['truth_included'] is False


@pytest.mark.parametrize('malformed',[False,True])
def test_mock_execution_and_completed_export_recovery_never_resend(pilot,malformed):
    _,directory,p=pilot
    grant=dict(state='approved',grant_message='UNIT TEST ONLY, this is NOT the real user grant',
        proposal_sha256=hashlib.sha256((directory/'proposal.json').read_bytes()).hexdigest(),max_estimated_cny='6',project_limit_cny='34')
    (directory/'authorization.json').write_text(json.dumps(grant),encoding='utf-8');requests=[]
    def factory(ledger,run_id,policy):
        def handler(request):
            requests.append(request);payload=json.loads(request.content);system=json.loads(payload['messages'][0]['content'])
            target='unused-in-extraction' if system['stage']=='extract' else system['schema']['properties']['source_statement']['properties']['transaction_id']['const']
            answer=OfflineDeliveryProbe(target).chat(payload['messages'])
            if malformed and len(requests)==1:answer={'role':'assistant','content':'not valid JSON'}
            return httpx.Response(200,json=dict(id='unit-response',model=WORLD_MODEL,choices=[dict(message=answer,finish_reason='stop')],
                usage=dict(prompt_tokens=100,completion_tokens=20,total_tokens=120)))
        http=httpx.Client(transport=httpx.MockTransport(handler))
        client=SiliconFlowClient('UNIT-ONLY-NOT-A-REAL-CREDENTIAL',policy=policy,ledger=ledger,run_id=run_id,http_client=http)
        client._owned_http=True
        return client
    r=execute(directory,live=True,client_factory=factory)
    expected_deliveries=35 if malformed else 36;expected_requests=71 if malformed else 72
    assert r['complete'] and r['delivered']==expected_deliveries and len(requests)==expected_requests
    assert r['external_model_requests']==0 and r['development_gate_only'] is None and not r['formal_agent_value_claim']
    assert all(len(req.content)<=60000 for req in requests)
    assert all(b'expected_source_statement' not in req.content and b'controller' not in req.content for req in requests)
    result=next(path for path in (directory/'live/results').glob('*.json') if not malformed or json.loads(path.read_text(encoding='utf-8'))['error']=='model_json');original=json.loads(result.read_text(encoding='utf-8'))
    result.rename(result.with_suffix('.archived-mock-export'))
    rerun=execute(directory,live=True,client_factory=factory)
    restored=json.loads(result.read_text(encoding='utf-8'))
    assert rerun['delivered']==expected_deliveries and len(requests)==expected_requests
    assert restored['delivered']==original['delivered'] and restored['report']==original['report']
    if malformed:assert restored['error_family']=='model_protocol' and restored['ledger']['unknown_cost_requests']==0
    assert len(list((directory/'live').glob('summary-*.json')))==2
    packet=next((directory/'source-packets').glob('*.json'));before=packet.read_bytes();packet.write_bytes(before+b' ')
    try:
        with pytest.raises(ValueError,match='Frozen source packet changed'):execute(directory,live=True,client_factory=factory)
        assert len(requests)==expected_requests
    finally:packet.write_bytes(before)
