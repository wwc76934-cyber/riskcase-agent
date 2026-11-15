"""Original-document MCP server; model and evaluation gold are absent here."""
import argparse
import asyncio
import json
from pathlib import Path
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent, CallToolResult
from app.investigation.contracts import OUTPUT, digest
from app.investigation.dossier import BoundSource, INPUTS, DESCRIPTIONS, GROUPS


async def serve(args):
    snapshot = json.loads(args.snapshot.read_text(encoding='utf-8'))
    if digest(snapshot) != args.sha256:
        raise ValueError('Snapshot changed')
    source = BoundSource(snapshot)
    server = Server('dossier-' + args.kind, version='1.0.0')
    names = GROUPS[args.kind]

    @server.list_tools()
    async def tools():
        return [Tool(name=n, description=DESCRIPTIONS[n], inputSchema=INPUTS[n], outputSchema=OUTPUT) for n in names]

    @server.call_tool()
    async def call(name, arguments):
        if name not in names:
            raise ValueError('Capability denied')
        result = source.call(name, arguments)
        return CallToolResult(content=[TextContent(type='text', text=json.dumps(result, ensure_ascii=False))], structuredContent=result)

    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--kind', choices=list(GROUPS), required=True)
    p.add_argument('--snapshot', type=Path, required=True)
    p.add_argument('--sha256', required=True)
    asyncio.run(serve(p.parse_args()))
