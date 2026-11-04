import asyncio,json
from tests.test_v3_source_access import runtime,start
from tests.test_v3_trajectory import AdaptiveProtocolClient
from app.investigation.v3.mcp_trajectory import run_case_mcp,SourceTransportUncertain
from app.investigation.v3.trajectory import run_case


def test_native_tool_agent_uses_real_mcp_for_all_three_time_stages(tmp_path,runtime):
    _,_,source=start(tmp_path,runtime);snapshot=tmp_path/'runtime.json';snapshot.write_text(json.dumps(runtime),encoding='utf-8');clients=[]
    def factory(stage):client=AdaptiveProtocolClient();clients.append(client);return client
    async def run():
        with (tmp_path/'mcp.log').open('w',encoding='utf-8') as log:return await run_case_mcp(source,snapshot,'B2',factory,errlog=log)
    result=asyncio.run(run())
    assert result['complete'] and all(p['report'] for p in result['phases'])
    assert result['source_transport']=='official_mcp_stdio' and set(result['mcp_handshake']['tools'])=={'v3_context','v3_read'}
    assert result['evidence_kind']=='scripted_protocol_probe' and not result['formal_agent_value_claim']
    assert len(clients)==3 and source.position()['stage']==2


def test_unknown_source_transport_stops_the_case_without_advancing_or_model_calls(tmp_path,runtime):
    _,_,source=start(tmp_path,runtime)
    class UnknownPort:
        def __getattr__(self,name):return getattr(source,name)
        def call(self,*args,**kwargs):raise SourceTransportUncertain('host-operation')
    result=run_case(UnknownPort(),'B0')
    assert not result['complete'] and result['blocked_uncertain_request'] and result['phases'][0]['blocked_uncertain_source']
    assert source.position()['stage']==0 and result['phases'][0]['protocol_request_attempts']==0
