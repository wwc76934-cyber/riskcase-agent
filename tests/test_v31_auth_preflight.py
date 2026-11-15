import json
import httpx
import pytest
from app.investigation.v3.auth_preflight import probe_project_credential

KEY='sk-unit-test-auth-preflight'
class UnreadBody(httpx.SyncByteStream):
    def __iter__(self):raise AssertionError('Do not read account response bodies')
    def close(self):pass

@pytest.mark.parametrize('statuses,verdict,permit',[
    ([400,401],'credential_distinguished',True),([404,401],'credential_distinguished',True),
    ([401,401],'credential_rejected',False),([403,401],'credential_rejected',False),
    ([200,401],'inconclusive',False),([404,404],'inconclusive',False),([302,401],'inconclusive',False)])
def test_negative_control_is_bounded_read_only_and_never_claims_generation_permission(statuses,verdict,permit):
    calls=[]
    def handle(request):
        calls.append(request)
        return httpx.Response(statuses[len(calls)-1],stream=UnreadBody(),headers={'Location':'https://example.invalid/do-not-follow'})
    with httpx.Client(transport=httpx.MockTransport(handle),follow_redirects=True) as client:
        result=probe_project_credential(KEY,http_client=client)
    assert len(calls)==2 and calls[0].url==calls[1].url
    assert all(r.method=='GET' and r.url.host=='api.siliconflow.cn' and r.url.path.startswith('/v1/batches/batch_') for r in calls)
    assert calls[0].headers['Authorization']=='Bearer '+KEY
    assert calls[1].headers['Authorization']!='Bearer '+KEY
    assert result['verdict']==verdict and result['permit_separately_authorized_attempt'] is permit
    assert not result['generation_permission_proven'] and result['generation_requests']==0
    assert not result['account_objects_listed'] and not result['response_bodies_read'] and KEY not in json.dumps(result)

def test_network_error_is_not_a_reason_to_attempt_a_paid_call():
    calls=[]
    def handle(request):calls.append(request);raise httpx.ReadTimeout(KEY,request=request)
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:result=probe_project_credential(KEY,http_client=client)
    assert len(calls)==1 and result['verdict']=='transport_error'
    assert not result['permit_separately_authorized_attempt'] and KEY not in json.dumps(result)
