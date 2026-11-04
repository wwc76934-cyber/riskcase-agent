import json,sqlite3
import httpx,pytest
from app.evaluation.v3_tasks import build
from app.evaluation.v3_delivery_pilot import prepare as original_prepare,freeze
from app.evaluation.v3_typed_repair import prepare,execute,save,load,sha,checked
from app.evaluation.v3_pilot_ledger import PilotLedger
from app.llm.cost_ledger import CostLedger
from app.llm.siliconflow_client import SiliconFlowClient
from tests.test_v3_pilot_ledger import reserve
from tests.test_v3_trajectory import AdaptiveProtocolClient


def prepared(tmp_path):
    dataset=tmp_path/'data';build(dataset);parent=tmp_path/'parent';proposal=original_prepare(dataset,parent)
    book=tmp_path/'book.sqlite';CostLedger(book)
    with sqlite3.connect(book) as db:db.execute('INSERT INTO budget_holds VALUES (?,?,?,?)',('test-prior','UNIT',proposal['project_before_pilot_committed_cny'],'UNIT synthetic prior accounting only'))
    grant=dict(state='approved',grant_message='UNIT parent only, not actual user consent',proposal_sha256=sha(parent/'proposal.json'),max_estimated_cny='6',project_limit_cny='34')
    save(parent/'authorization.json',grant);ledger=PilotLedger(book,parent/'proposal.json',grant)
    reservation=reserve(ledger,ledger.run_id(proposal['case_ids'][0],1,0));ledger.settle(reservation,estimate='.01',usage={})
    for case in proposal['case_ids']:
        for repeat in (1,2):
            for stage in range(3):ledger.close_phase(ledger.run_id(case,repeat,stage),'model_failure')
    (parent/'live').mkdir();save(parent/'live/summary.json',dict(complete=True,recorded_phase_jobs=36,external_model_requests=1));freeze(parent,dataset,project_ledger_path=book)
    out=tmp_path/'repair';prepare(parent,dataset,out,book)
    return out,book


def test_prepare_is_offline_and_old_grant_is_not_reused(tmp_path):
    out,book=prepared(tmp_path);proposal=load(out/'proposal.json')
    assert proposal['max_requests']==36 and proposal['whole_round_limit_cny']=='6' and proposal['max_request_reservation_cny']=='0.031104'
    assert proposal['largest_preflight_context_bound_bytes']<=20000 and checked(out)
    assert not (out/'authorization.json').exists()
    with pytest.raises(ValueError,match='Explicit live'):execute(out)
    with pytest.raises(FileNotFoundError):execute(out,live=True,client_factory=lambda *a:pytest.fail('No new user grant'))
    with sqlite3.connect(book) as db:assert db.execute('SELECT COUNT(*) FROM reservations').fetchone()[0]==1


def test_mock_provider_through_actual_mcp_keeps_all_phases_and_never_claims_value(tmp_path):
    out,book=prepared(tmp_path);save(out/'authorization.json',dict(state='approved',grant_message='UNIT additional scope only, not real consent',proposal_sha256=sha(out/'proposal.json'),max_estimated_cny='6',project_limit_cny='34'))
    clients=[];calls=[]
    def factory(ledger,run,policy):
        probe=AdaptiveProtocolClient()
        def handle(request):
            body=json.loads(request.content);calls.append(body)
            answer=probe.chat(body['messages'])
            return httpx.Response(200,json=dict(model=policy['model'],choices=[dict(message=answer)],usage=dict(prompt_tokens=20,completion_tokens=20,total_tokens=40)))
        http=httpx.Client(transport=httpx.MockTransport(handle));clients.append(http)
        return SiliconFlowClient('unit-test-key-not-real',policy=policy,ledger=ledger,run_id=run,http_client=http)
    try:
        result=execute(out,live=True,client_factory=factory)
        assert result['complete'] and result['delivered']==18 and result['complete_delivered_cases']==6
        assert result['recorded_model_request_reservations']==36 and len(calls)==36
        assert result['external_model_requests']==0 and result['development_gate_only'] is None and not result['formal_agent_value_claim']
        again=execute(out,live=True,client_factory=lambda *a:pytest.fail('Completed cases never reissue'))
        assert again['delivered']==18 and len(calls)==36
        assert not any('expected_source_statement' in json.dumps(c) for c in calls)
    finally:
        for http in clients:http.close()
