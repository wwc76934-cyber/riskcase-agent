"""Transport opt-in only; mocks do not prove provider schema support."""
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
import json
import httpx
import pytest
from app.investigation.v3.schema_client import SchemaDeliveryClient
from app.investigation.v3.delivery import request_for
from app.llm.siliconflow_client import SiliconFlowClient
from app.llm.cost_ledger import CostLedger,BudgetExceeded
from app.llm.glm_client import ProtocolError,LLMError

ROOT=Path(__file__).resolve().parents[1]
SCHEMA=dict(type='object',properties=dict(value=dict(type='string',enum=['unresolved','authorized','denied'])),
    required=['value'],additionalProperties=False)

@pytest.fixture
def factory(tmp_path):
    resources=[]
    def make(*,cls=SchemaDeliveryClient,status=200,policy_changes=None):
        calls=[];policy=json.loads((ROOT/'config/siliconflow-world-v2.json').read_text(encoding='utf-8'))
        policy.update(policy_changes or {})
        def handle(request):
            calls.append(request)
            return httpx.Response(status,json=dict(model=policy['model'],choices=[dict(message=dict(role='assistant',content='{"value":"unresolved"}'))],
                usage=dict(prompt_tokens=20,completion_tokens=10,total_tokens=30)))
        http=httpx.Client(transport=httpx.MockTransport(handle));resources.append(http)
        model=cls('unit-schema-key',policy=policy,ledger=CostLedger(tmp_path/('ledger-'+str(len(resources))+'.sqlite')),run_id='schema-unit',http_client=http)
        return model,calls
    yield make
    for resource in resources:resource.close()

def request(schema=None):return request_for('extract',deepcopy(SCHEMA if schema is None else schema),dict(untrusted='not a schema'))

def test_schema_is_on_wire_and_full_wire_size_is_reserved(factory):
    model,calls=factory();req=request();before=deepcopy(req)
    model.chat(**req);wire=json.loads(calls[0].content)
    assert req==before and wire['response_format']==dict(type='json_schema',json_schema=dict(name='riskcase_extract',strict=True,schema=SCHEMA))
    audit=model.snapshot()['request_audit'][0]
    assert audit['input_bytes']==len(calls[0].content)
    expected=model.estimated_cost(len(calls[0].content)+4096,4096+128)
    assert Decimal(audit['reserved_estimate_cny'])==expected

def test_legacy_and_default_world_clients_keep_original_formats(factory):
    model,calls=factory(cls=SiliconFlowClient);model.chat(**request())
    assert json.loads(calls[0].content)['response_format']==dict(type='json_object')
    model,calls=factory(policy_changes=dict(version=1,model='Pro/zai-org/GLM-5.1',max_tokens=1024,max_input_bytes=40000,request_timeout_seconds=45,run_deadline_seconds=180))
    with pytest.raises(ProtocolError,match='requires_v2'):model.chat(**request())
    assert not calls and not model.snapshot()['ledger']['entries']

@pytest.mark.parametrize('mutation',['role','stage','root','additional','invalid_type','remote_ref','user_only'])
def test_untrusted_or_invalid_schema_never_reaches_provider(factory,mutation):
    model,calls=factory();req=request();system=json.loads(req['messages'][0]['content'])
    if mutation=='role':req['messages'][0]['role']='user'
    if mutation=='stage':system['stage']='planner'
    if mutation=='root':system['schema']['type']='array'
    if mutation=='additional':system['schema'].pop('additionalProperties')
    if mutation=='invalid_type':system['schema']['properties']['value']['type']='invented'
    if mutation=='remote_ref':system['schema']['properties']['value']={'$ref':'https://example.invalid/private'}
    if mutation=='user_only':system.pop('schema')
    req['messages'][0]['content']=json.dumps(system)
    with pytest.raises(ProtocolError,match='host_delivery_schema'):model.chat(**req)
    assert not calls and not model.snapshot()['ledger']['entries']

def test_schema_bytes_are_checked_before_reservation_or_network(factory):
    model,calls=factory(policy_changes=dict(max_input_bytes=3000))
    # Plain json_object payload fits, but the complete schema-bearing wire does not.
    schema=deepcopy(SCHEMA);schema['description']='x'*1200;req=request(schema)
    default,calls_default=factory(cls=SiliconFlowClient,policy_changes=dict(max_input_bytes=3000))
    default.chat(**req);assert len(calls_default[0].content)<3000
    with pytest.raises(BudgetExceeded,match='input_byte_limit'):model.chat(**req)
    assert not calls and not model.snapshot()['ledger']['entries']

def test_provider_rejection_never_falls_back_or_retries(factory):
    model,calls=factory(status=400)
    with pytest.raises(LLMError,match='http_400'):model.chat(**request())
    assert len(calls)==1 and model.snapshot()['ledger']['unknown_cost_requests']==1
    assert json.loads(calls[0].content)['response_format']['type']=='json_schema'

def test_schema_delivery_cannot_be_misused_for_dynamic_tools(factory):
    model,calls=factory()
    with pytest.raises(ProtocolError,match='without_tools'):model.chat(request()['messages'],tools=[dict(type='function')])
    assert not calls and not model.snapshot()['ledger']['entries']
