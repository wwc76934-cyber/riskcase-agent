import json
import sqlite3
import httpx
import pytest
import importlib.util
from pathlib import Path
_spec=importlib.util.spec_from_file_location('v32_reviewed_controller',Path(__file__).resolve().parents[1]/'docs/evidence/v32-report-plan/reviewed_controller.py')
_module=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(_module)
inspect_rejection,prepare_review,resume=_module.inspect_rejection,_module.prepare_review,_module.execute
from app.investigation.v3.report_plan import ReportPlanContract
from app.evaluation.v3_typed_repair import execute,save,sha,load
from app.evaluation.v31_trial import prepare
from app.llm.siliconflow_client import SiliconFlowClient
from app.llm.cost_ledger import BudgetExceeded
from tests.test_v3_pilot_ledger import ledger,reserve
from tests.test_v3_typed_repair import prepared
from tests.test_v3_phase_store import probe_report


def rejection(l,p):
    case=p['case_ids'][0];run=l.run_id(case,1,0);rid=reserve(l,run)
    l.settle(rid);l.close_phase(run,'blocked_unknown');l.hold_uncertain_case(run)
    return dict(case_id=case,repeat=1,runtime=dict(blocked_uncertain_request=True,phases=[dict(stage=0,report=None,
        usage=dict(requests_sent=1,request_audit=[dict(reservation_id=rid,http_status=401,error_code='http_401',request_trace_id='unit-trace')]))]))


@pytest.mark.parametrize('mutation',['status','model','trace','report','role','hold'])
def test_only_exact_auth_rejection_with_conservative_hold_is_reviewable(tmp_path,mutation):
    l,p,_=ledger(tmp_path);r=rejection(l,p)
    inspect_rejection(l,r)
    phase=r['runtime']['phases'][0];a=phase['usage']['request_audit'][0]
    if mutation=='status': a['http_status']=500
    if mutation=='model': a['returned_model']='unexpected'
    if mutation=='trace': a.pop('request_trace_id')
    if mutation=='report': phase['report']={'not':'a rejection'}
    if mutation=='role': r['repeat']=2
    if mutation=='hold':
        with sqlite3.connect(l.path) as db: db.execute('UPDATE budget_holds SET amount_cny=?',('.01',))
    with pytest.raises(ValueError): inspect_rejection(l,r)


def test_recovery_skips_failed_trajectory_preserves_original_and_never_retries(tmp_path):
    _,book=prepared(tmp_path);out=tmp_path/'v31';prepare(tmp_path/'parent',tmp_path/'data',out,book,report_contract_factory=ReportPlanContract)
    save(out/'authorization.json',dict(state='approved',grant_message='UNIT ONLY not real authority',proposal_sha256=sha(out/'proposal.json'),max_estimated_cny='3',project_limit_cny='34'))
    clients=[];calls=[]
    def factory(l,run,policy):
        def handle(request):
            calls.append(run)
            if len(calls)==1: return httpx.Response(401,json={'message':'UNIT invalid token'})
            body=json.loads(request.content);system=json.loads(body['messages'][0]['content'])
            if system['stage']=='extract':
                facts=[]
                for b in system['schema']['properties']['facts']['items'].get('anyOf',[]):
                    props=b['properties'];f=dict(predicate=props['predicate']['const'],refs=[props['refs']['items']['enum'][0]])
                    if 'value' in props:f['value']='success' if f['predicate']=='authentication' else 'unresolved'
                    facts.append(f)
                payload=dict(facts=facts)
            else:
                payload=probe_report()
                for step in payload['investigation']['recommendations']:
                    step.pop('action');step['action_code']='A1'
            return httpx.Response(200,json=dict(model=policy['model'],choices=[dict(message=dict(role='assistant',content=json.dumps(payload)))],usage=dict(prompt_tokens=20,completion_tokens=20,total_tokens=40)))
        h=httpx.Client(transport=httpx.MockTransport(handle));clients.append(h)
        return SiliconFlowClient('unit-not-real-key',policy=policy,ledger=l,run_id=run,http_client=h)
    try:
        first=execute(out,live=True,client_factory=factory)
        assert first['external_model_requests']==0 and len(calls)==1
        original=next((out/'live/cases').glob('*.json'));fingerprint=sha(original)
        with pytest.raises(FileNotFoundError):resume(out,live=True,client_factory=factory)
        with pytest.raises(ValueError,match='authentication check'):
            prepare_review(out,credential_check={},operator='test',user_configuration_message='UNIT')
        prepare_review(out,credential_check=dict(http_status=200,model_available=True,generation_requests=0),operator='test-controller',user_configuration_message='UNIT config updated')
        result=resume(out,live=True,client_factory=factory)
        assert len(calls)==67 and calls.count(calls[0])==1
        assert result['delivered']==33 and result['complete_delivered_cases']==11
        assert result['recorded_phases']==34 and result['planned_phases']==36
        assert result['missing_phases_count_as_unsuccessful']==2
        assert result['development_thresholds'] is None and result['external_model_requests']==0
        assert result['financial_snapshot']['case_committed_estimate_cny']=='0.5000000'
        assert sha(original)==fingerprint and load(out/'live/summary.json')==first
        again=resume(out,live=True,client_factory=lambda *a:pytest.fail('Never resend'))
        assert again['delivered']==33
        # Frozen controller cannot be silently replaced after operator review.
        review=load(out/'recovery-review.json');review['controller_sha256']='bad'
        (out/'recovery-review.json').write_text(json.dumps(review),encoding='utf-8')
        with pytest.raises(ValueError,match='artifacts changed'):resume(out,live=True,client_factory=factory)
    finally:
        for h in clients:h.close()
