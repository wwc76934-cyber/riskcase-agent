"""Read-only query tools served through the same official MCP transport."""
import argparse
import asyncio
import json
from pathlib import Path
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent, CallToolResult
from app.investigation.contracts import OUTPUT, digest
from app.investigation.inquiry import Source, INPUTS, DESCRIPTIONS, GROUPS


async def serve(args):
    snapshot=json.loads(args.snapshot.read_text(encoding='utf-8'))
    if digest(snapshot)!=args.sha256:raise ValueError('Snapshot changed')
    inputs,descriptions,groups=INPUTS,DESCRIPTIONS,GROUPS
    prefix='fraud-inquiry-'
    if snapshot.get('task_contract')=='fraud-world-v1':
        from app.investigation.fraud_world import World,INPUTS as inputs,DESCRIPTIONS as descriptions,GROUPS as groups
        source=World(snapshot);prefix='fraud-world-'
    else:source=Source(snapshot)
    names=groups[args.kind]
    server=Server(prefix+args.kind,version='1.0.0')

    @server.list_tools()
    async def tools():
        return [Tool(name=n,description=descriptions[n],inputSchema=inputs[n],outputSchema=OUTPUT) for n in names]

    @server.call_tool()
    async def call(name,arguments):
        if name not in names:raise ValueError('Capability denied')
        result=source.call(name,arguments)
        return CallToolResult(content=[TextContent(type='text',text=json.dumps(result,ensure_ascii=False))],structuredContent=result)

    async with stdio_server() as (read,write):
        await server.run(read,write,server.create_initialization_options())


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--kind',choices=list(GROUPS),required=True)
    p.add_argument('--snapshot',type=Path,required=True)
    p.add_argument('--sha256',required=True)
    asyncio.run(serve(p.parse_args()))
