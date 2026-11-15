"""Explicit local contract adapter, not an MCP transport or model."""
from contextlib import asynccontextmanager
from types import SimpleNamespace
from app.investigation.mcp_server import BoundSource
from app.investigation.contracts import INPUTS


class LocalSession:
    def __init__(self,snapshot):self.source=BoundSource(snapshot)
    async def call_tool(self,name,args):
        return SimpleNamespace(isError=False,structuredContent=self.source.call(name,args))


@asynccontextmanager
async def local_connect(path,snapshot,*,errlog):
    session=LocalSession(snapshot)
    yield {name:session for name in INPUTS},{'transport':'in_process_contract_adapter_NOT_MCP'}
