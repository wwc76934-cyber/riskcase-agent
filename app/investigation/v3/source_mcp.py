"""Only current-source context and bounded reads, through official MCP stdio."""
import argparse,asyncio,json
from copy import deepcopy
from pathlib import Path
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool,TextContent,CallToolResult
from app.investigation.contracts import obj,digest
from app.service.store import Blocked,LeaseLost
from app.service.v3_sources import SourceAccess
from app.investigation.v3.output import OutputFailure

MODEL_INPUTS={'v3_context':obj({},[]),'v3_read':obj(dict(source_ids=dict(type='array',minItems=1,maxItems=12,uniqueItems=True,
    items=dict(type='string',minLength=1,maxLength=128))),['source_ids'])}
INPUTS=deepcopy(MODEL_INPUTS)
for schema in INPUTS.values():schema['properties']['operation_key']=dict(type='string',minLength=1,maxLength=128,description='Host-generated replay key. Not a model business argument.')
OUTPUT=obj(dict(ok=dict(type='boolean'),frame=dict(type=['object','null']),evidence_id=dict(type=['string','null']),
    cached_source_ids=dict(type='array',items=dict(type='string')),catalog=dict(type='object'),error=dict(type=['object','null']),operation_replayed=dict(type='boolean')),
    ['ok','frame','evidence_id','cached_source_ids','catalog','error','operation_replayed'])
DESCRIPTIONS={'v3_context':'Current simulated investigation scope, available-source metadata and remaining acquisition budget. No future originals.',
              'v3_read':'Read currently available immutable simulated originals within source, dependency, time and cost limits. Cached originals do not spend acquisition units twice.'}


async def serve(args):
    snapshot=json.loads(args.snapshot.read_text(encoding='utf-8'))
    if digest(snapshot)!=args.sha256:raise ValueError('Snapshot fingerprint changed')
    source=SourceAccess(args.database,dict(id=args.job_id,owner=args.owner,generation=args.generation),snapshot,max_tools=args.max_tools)
    server=Server('riskcase-v3-local-sources',version='1.0.0')

    @server.list_tools()
    async def tools():return [Tool(name=k,description=DESCRIPTIONS[k],inputSchema=v,outputSchema=OUTPUT) for k,v in INPUTS.items()]

    @server.call_tool()
    async def call(name,arguments):
        arguments={} if arguments is None else dict(arguments);operation_key=arguments.pop('operation_key',None)
        try:result=source.call(name,arguments,operation_key=operation_key)
        except OutputFailure as exc:
            result=dict(ok=False,frame=None,evidence_id=None,cached_source_ids=[],catalog={},error=dict(code=exc.code),operation_replayed=False)
        except (Blocked,LeaseLost):
            # Ownership/deadline failures must not reveal worker credentials.
            result=dict(ok=False,frame=None,evidence_id=None,cached_source_ids=[],catalog={},error=dict(code='source_execution_fenced'),operation_replayed=False)
        return CallToolResult(content=[TextContent(type='text',text=json.dumps(result,ensure_ascii=False))],structuredContent=result,isError=not result['ok'])

    async with stdio_server() as (read,write):await server.run(read,write,server.create_initialization_options())


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('snapshot','database'):parser.add_argument('--'+name,type=Path,required=True)
    for name in ('sha256','job-id','owner'):parser.add_argument('--'+name,required=True)
    parser.add_argument('--generation',type=int,required=True);parser.add_argument('--max-tools',type=int,default=20)
    asyncio.run(serve(parser.parse_args()))


if __name__=='__main__':main()
