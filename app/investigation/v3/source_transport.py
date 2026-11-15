"""Validated V3 MCP client; no automatic RPC retry or model credential access."""
from contextlib import asynccontextmanager
from datetime import timedelta
import os,sys
from pathlib import Path
from jsonschema import validate
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
from app.investigation.contracts import digest
from app.investigation.v3.source_mcp import INPUTS,MODEL_INPUTS,OUTPUT


@asynccontextmanager
async def connect(snapshot_path,snapshot,database,claim,*,errlog,max_tools=20):
    root=Path(__file__).resolve().parents[3]
    params=StdioServerParameters(command=sys.executable,args=['-X','utf8','-B','-m','app.investigation.v3.source_mcp',
        '--snapshot',str(Path(snapshot_path).resolve()),'--sha256',digest(snapshot),'--database',str(Path(database).resolve()),
        '--job-id',claim['id'],'--owner',claim['owner'],'--generation',str(claim['generation']),'--max-tools',str(max_tools)],
        cwd=str(root),env={'PYTHONPATH':os.pathsep.join(p for p in sys.path if p),'PYTHONUTF8':'1'})
    async with stdio_client(params,errlog=errlog) as (read,write):
        async with ClientSession(read,write,read_timeout_seconds=timedelta(seconds=20)) as session:
            init=await session.initialize();catalog=await session.list_tools()
            if catalog.nextCursor is not None or len(catalog.tools)!=len(INPUTS) or {t.name for t in catalog.tools}!=set(INPUTS):raise ValueError('Unexpected V3 capabilities')
            for t in catalog.tools:
                if t.inputSchema!=INPUTS[t.name] or t.outputSchema!=OUTPUT:raise ValueError('V3 MCP schema drift')
            yield session,dict(protocol=init.protocolVersion,server=init.serverInfo.model_dump(),tools=sorted(INPUTS))


async def call(session,name,arguments,*,operation_key=None):
    if name not in INPUTS:raise ValueError('Disabled V3 capability')
    validate(arguments,MODEL_INPUTS[name])
    wire=dict(arguments)
    if operation_key is not None:wire['operation_key']=operation_key
    validate(wire,INPUTS[name])
    result=await session.call_tool(name,wire)
    validate(result.structuredContent,OUTPUT)
    if bool(result.isError)!=(not result.structuredContent['ok']):raise ValueError('V3 MCP error envelope mismatch')
    return result.structuredContent
