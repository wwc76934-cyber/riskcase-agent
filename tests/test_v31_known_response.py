import json
from pathlib import Path
import sqlite3

import httpx
import pytest

from app.investigation.v3.delivery import StagedDelivery,request_for
from app.investigation.contracts import digest
from app.investigation.v3.known_response_client import KnownResponseDeliveryClient
from app.investigation.v3.output import OutputFailure
from app.investigation.v3.selected_fact_report import SelectedFactReportContract
from app.investigation.v3.trajectory import run_case
from app.llm.glm_client import LLMError,ProtocolError
from app.service.v3_phase_store import DeliveryPhase
from app.service.v3_sources import SourceAccess
from tests.test_v3_pilot_ledger import ledger as funding
from tests.test_v3_source_access import ledger,runtime,start


def client_factory(book,proposal,transport):
    policy=json.loads(Path('config/siliconflow-world-v2.json').read_text(encoding='utf-8'))
    policy.update(max_requests=2,max_input_bytes=24000,run_budget_cny='.50',project_budget_cny='34')
    return lambda stage:KnownResponseDeliveryClient('unit-test-key',policy=policy,ledger=book,
        run_id=book.run_id(proposal['case_ids'][0],1,stage),http_client=transport)


def empty_response(model):
    return dict(id='offline-v34-shape',model=model,
        choices=[dict(finish_reason='stop',message=dict(role='assistant',content=''))],
        usage=dict(prompt_tokens=1905,completion_tokens=27,total_tokens=1932))


def test_known_empty_response_is_terminal_for_phase_not_uncertain_for_case(tmp_path,runtime):
    # Match the development pilot's full-current-materials path, which does not
    # expose native tool calls to the structured delivery client.
    for record in runtime['records']:record['cost_units']=0
    runtime['records_sha256']=digest(runtime['records'])
    _,_,source=start(tmp_path,runtime);book,proposal,_=funding(tmp_path)
    requests=[]
    def handle(request):
        wire=json.loads(request.content)
        requests.append(wire)
        return httpx.Response(200,json=empty_response(wire['model']))
    with httpx.Client(transport=httpx.MockTransport(handle)) as transport:
        factory=client_factory(book,proposal,transport)
        result=run_case(source,'B1_staged',factory,report_contract_factory=SelectedFactReportContract)
        assert not result['blocked_uncertain_request'] and len(result['phases'])==3
        assert len(requests)==3 and all(p['error']['code']=='model_empty_content' for p in result['phases'])
        assert all(p['report'] is None and p['usage']['ledger']['unknown_cost_requests']==0 for p in result['phases'])
        again=run_case(source,'B1_staged',lambda _:pytest.fail('Do not dispatch again'),
            report_contract_factory=SelectedFactReportContract)
        assert again['phases']==result['phases']
    with sqlite3.connect(source.path) as db:
        assert db.execute('SELECT DISTINCT state FROM v3_phase_calls').fetchall()==[('completed',)]
    for original in tmp_path.glob('v3-model-records/*/provider-01.json'):
        assert json.loads(original.read_text(encoding='utf-8'))['message']['content']==''


def test_interruption_after_known_empty_receipt_replays_without_request(tmp_path,runtime):
    now=[100]
    store,claim,source=start(tmp_path,runtime,clock=lambda:now[0]);book,proposal,_=funding(tmp_path)
    acquired=ledger(runtime,source)
    contract=SelectedFactReportContract(acquired.target_id)
    phase=DeliveryPhase(source,0)
    def stop(kind,ordinal):
        if kind=='response_saved':raise SystemExit('Offline interruption')
    with httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,json=empty_response(proposal['model'])))) as transport:
        with pytest.raises(SystemExit):
            StagedDelivery(phase,phase.claim,acquired,client_factory(book,proposal,transport)(0),max_requests=2,
                hook=stop,lease_seconds=30,report_contract=contract).run()
    now[0]=131
    replacement=store.claim('new-owner',lease_seconds=120,deadline_seconds=600,mode='offline')
    resumed=DeliveryPhase(SourceAccess(source.path,replacement,runtime,clock=lambda:now[0]),0)
    with httpx.Client(transport=httpx.MockTransport(lambda _:pytest.fail('Do not dispatch again'))) as transport:
        with pytest.raises(OutputFailure,match='model_empty_content'):
            StagedDelivery(resumed,resumed.claim,acquired,client_factory(book,proposal,transport)(0),
                max_requests=2,report_contract=contract).run()
    assert resumed.load(resumed.identity)['replayed_responses']==1


@pytest.mark.parametrize('missing',['usage','model'])
def test_unknown_usage_or_model_is_not_promoted_to_completed(tmp_path,runtime,missing):
    _,_,source=start(tmp_path,runtime);book,proposal,_=funding(tmp_path)
    acquired=ledger(runtime,source);phase=DeliveryPhase(source,0)
    payload=empty_response(proposal['model']);payload.pop(missing)
    with httpx.Client(transport=httpx.MockTransport(lambda _:httpx.Response(200,json=payload))) as transport:
        client=client_factory(book,proposal,transport)(0)
        with pytest.raises(ProtocolError):
            StagedDelivery(phase,phase.claim,acquired,client,max_requests=2,
                report_contract=SelectedFactReportContract(acquired.target_id)).run()
        assert client.snapshot()['ledger']['unknown_cost_requests']==1
    with sqlite3.connect(source.path) as db:
        assert db.execute('SELECT DISTINCT state FROM v3_phase_calls').fetchall()==[('uncertain',)]


def test_401_audit_retains_only_structured_diagnostics_and_no_retry(tmp_path,runtime):
    _,_,source=start(tmp_path,runtime);book,proposal,_=funding(tmp_path)
    acquired=ledger(runtime,source);phase=DeliveryPhase(source,0)
    trace='ti_0123456789abcdef0123456789abcdef'
    calls=[]
    def reject(request):
        calls.append(request)
        return httpx.Response(401,headers={'x-siliconcloud-trace-id':trace},
            json={'code':30001,'message':'unit-test-key raw message must not be retained',
                  'data':{'usage':{'total_tokens':0}}})
    with httpx.Client(transport=httpx.MockTransport(reject)) as transport:
        client=client_factory(book,proposal,transport)(0)
        with pytest.raises(LLMError,match='^http_401$'):
            StagedDelivery(phase,phase.claim,acquired,client,max_requests=2,
                report_contract=SelectedFactReportContract(acquired.target_id)).run()
    assert len(calls)==1
    with sqlite3.connect(source.path) as db:
        state,saved=db.execute('SELECT state,audit FROM v3_phase_calls').fetchone()
    audit=json.loads(saved)
    assert state=='uncertain' and audit['ledger']['unknown_cost_requests']==1
    assert audit['request_audit'][0]['provider_trace_id']==trace
    assert audit['request_audit'][0]['provider_error_code']==30001
    assert 'unit-test-key' not in saved and 'raw message' not in saved


@pytest.mark.parametrize('read_failure',[False,True])
def test_optional_rejection_body_does_not_change_http_outcome(tmp_path,read_failure):
    book,proposal,_=funding(tmp_path)
    class Body(httpx.SyncByteStream):
        def __iter__(self):
            if read_failure:
                raise httpx.ReadTimeout('unit-test-key')
            pytest.fail('Oversized rejection body must remain unread')
    response=httpx.Response(401,headers={'content-type':'application/json',
        'content-length':'128' if read_failure else '4097',
        'x-siliconcloud-trace-id':'unit-test-key'},stream=Body())
    with httpx.Client(transport=httpx.MockTransport(lambda _:response)) as transport:
        client=client_factory(book,proposal,transport)(0)
        request=request_for('extract',dict(type='object',properties={},additionalProperties=False),{})
        with pytest.raises(LLMError,match='^http_401$'):
            client.chat(**request)
    audit=client.snapshot()
    assert audit['ledger']['unknown_cost_requests']==1
    assert 'provider_error_code' not in audit['request_audit'][0]
    assert 'provider_trace_id' not in audit['request_audit'][0]
