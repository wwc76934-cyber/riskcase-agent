from copy import deepcopy
import json
from pathlib import Path
import httpx
import pytest
from app.llm.siliconflow_client import SiliconFlowClient,MODEL,WORLD_MODEL,validate_policy
from app.llm.cost_ledger import CostLedger
from app.llm.glm_client import ProtocolError

ROOT=Path(__file__).resolve().parents[1]


def test_new_world_policy_supports_full_baseline_without_weakening_v1(tmp_path):
    policy=json.loads((ROOT/'config/siliconflow-world-v2.json').read_text())
    requests=[]
    def handler(request):
        requests.append(request)
        return httpx.Response(200,json=dict(id='mock',model=WORLD_MODEL,usage=dict(prompt_tokens=100,completion_tokens=20,total_tokens=120),choices=[dict(message=dict(role='assistant',content='ok'))]))
    client=SiliconFlowClient('test-only',policy=policy,ledger=CostLedger(tmp_path/'ledger.sqlite'),run_id='mock-world',http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert client.chat([dict(role='user',content='x'*95000)])['content']=='ok'
    assert len(requests)==1 and json.loads(requests[0].content)['max_tokens']==4096
    old=json.loads((ROOT/'config/siliconflow-live-v1.json').read_text());old['max_input_bytes']=180000
    with pytest.raises(ValueError):validate_policy(old)


def test_model_substitution_keeps_unknown_charge_and_does_not_retry(tmp_path):
    policy=json.loads((ROOT/'config/siliconflow-world-v2.json').read_text())
    calls=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(200,json=dict(model=MODEL,usage=dict(prompt_tokens=100,completion_tokens=20,total_tokens=120)))
    client=SiliconFlowClient('test-only',policy=policy,ledger=CostLedger(tmp_path/'ledger.sqlite'),run_id='wrong-model',http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(ProtocolError,match='returned_model_changed'):client.chat([])
    assert len(calls)==1 and client.snapshot()['ledger']['unknown_cost_requests']==1


def test_world_reasoning_is_billed_within_output_and_failure_response_survives(tmp_path):
    from app.investigation.recording import RecordedClient
    policy=json.loads((ROOT/'config/siliconflow-world-v2.json').read_text())
    def handler(request):
        return httpx.Response(200,json=dict(id='observed-boundary-fixture',model=WORLD_MODEL,
            usage=dict(prompt_tokens=100,completion_tokens=200,total_tokens=300,completion_tokens_details=dict(reasoning_tokens=129)),
            choices=[dict(message=dict(role='assistant',content='public answer',reasoning_content='must not be retained'))]))
    client=SiliconFlowClient('test-only',policy=policy,ledger=CostLedger(tmp_path/'ledger.sqlite'),run_id='reasoning',http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    recording=tmp_path/'recording';recording.mkdir();wrapped=RecordedClient(client,recording)
    assert wrapped.chat([])['content']=='public answer'
    assert client.snapshot()['ledger']['entries'][0]['estimated_cny']=='0.00064'
    capture=json.loads((recording/'provider-01.json').read_text());assert 'must not be retained' not in json.dumps(capture)
    # A different returned model still fails, but its outward response is retained.
    bad=deepcopy(policy);bad['max_tokens']=64
    client=SiliconFlowClient('test-only',policy=bad,ledger=CostLedger(tmp_path/'other.sqlite'),run_id='over-budget',http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    wrapped=RecordedClient(client,recording)
    with pytest.raises(ProtocolError,match='reported_output_exceeds'):wrapped.chat([])
    assert json.loads((recording/'provider-02.json').read_text())['message']['content']=='public answer'
    assert (recording/'request-01.json').exists() and (recording/'request-02.json').exists()


@pytest.mark.parametrize('change',[dict(version=3),dict(model=MODEL),dict(max_tokens=4097),dict(input_rate_cny_per_million='0.01')])
def test_unbounded_or_underpriced_world_policies_rejected(change):
    policy=json.loads((ROOT/'config/siliconflow-world-v2.json').read_text());policy.update(change)
    with pytest.raises(ValueError):validate_policy(policy)
