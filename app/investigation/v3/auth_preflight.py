"""Read-only negative-control authentication indication; never a paid probe.

GET only a newly generated nonexistent batch ID, never list account objects or
read response bodies. Distinguishing 400/404 from invalid-control 401 is NOT a
proof of generation permissions. A separate frozen user grant is still required.
"""
from uuid import uuid4
import httpx
from app.llm.credentials import validate_key

def probe_project_credential(key,*,http_client=None):
    validate_key(key)
    owned=http_client is None
    client=http_client or httpx.Client(transport=httpx.HTTPTransport(retries=0),follow_redirects=False)
    url='https://api.siliconflow.cn/v1/batches/batch_'+uuid4().hex
    result=dict(contract='v31-negative-control-auth-v1',project_http_status=None,invalid_control_http_status=None,
        generation_requests=0,account_objects_listed=0,response_bodies_read=False,
        generation_permission_proven=False,verdict='inconclusive',permit_separately_authorized_attempt=False)
    try:
        for field,credential in [('project_http_status',key),('invalid_control_http_status','riskcase-invalid-auth-probe')]:
            with client.stream('GET',url,headers={'Authorization':'Bearer '+credential},timeout=10,follow_redirects=False) as response:
                result[field]=response.status_code
        if result['project_http_status'] in (401,403):result['verdict']='credential_rejected'
        elif result['project_http_status'] in (400,404) and result['invalid_control_http_status']==401:
            result.update(verdict='credential_distinguished',permit_separately_authorized_attempt=True)
    except httpx.HTTPError:
        result['verdict']='transport_error'
    finally:
        if owned:client.close()
    return result
