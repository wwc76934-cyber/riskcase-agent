"""Run the reference-free case executor through real official MCP stdio."""
import asyncio
from concurrent.futures import TimeoutError as FutureTimeout
from jsonschema import validate
from app.investigation.v3.source_transport import connect,call
from app.investigation.v3.source_mcp import MODEL_INPUTS
from app.investigation.v3.trajectory import run_case


class SourceTransportUncertain(RuntimeError):
    code='source_transport_unknown'
    def __init__(self,operation_key=None):
        self.operation_key=operation_key
        super().__init__('Source RPC status unknown; inspect the operation journal, do not automatically retry')


class MCPSourcePort:
    def __init__(self,source,session,loop):self.source,self.session,self.loop=source,session,loop
    def __getattr__(self,name):return getattr(self.source,name)
    def call(self,name,arguments,*,operation_key=None):
        if name not in MODEL_INPUTS:raise ValueError('Disabled source capability')
        validate(arguments,MODEL_INPUTS[name])
        future=asyncio.run_coroutine_threadsafe(call(self.session,name,arguments,operation_key=operation_key),self.loop)
        try:return future.result(timeout=25)
        except Exception:raise SourceTransportUncertain(operation_key) from None


async def run_case_mcp(source,snapshot_path,condition,client_factory=None,*,errlog,evidence_kind='scripted_protocol_probe',report_contract_factory=None,tier_gate=False):
    # SDK cancellation scopes enter and exit on this same task. Synchronous
    # model work runs in another thread; MCP calls stay on the owning loop.
    async with connect(snapshot_path,source.snapshot,source.path,source.claim,errlog=errlog,max_tools=source.max_tools) as (session,handshake):
        port=MCPSourcePort(source,session,asyncio.get_running_loop())
        result=await asyncio.to_thread(run_case,port,condition,client_factory,evidence_kind=evidence_kind,report_contract_factory=report_contract_factory,tier_gate=tier_gate)
        return dict(result,source_transport='official_mcp_stdio',mcp_handshake=handshake)
