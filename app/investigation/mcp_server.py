"""Two read-only MCP servers. Host binds one verified snapshot at process launch."""
import argparse
import asyncio
from datetime import datetime
import json
from pathlib import Path

from jsonschema import validate
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent, CallToolResult
from app.investigation.contracts import INPUTS, OUTPUT, DESCRIPTIONS, digest


class BoundSource:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        cutoff = datetime.fromisoformat(snapshot['cutoff'])
        if cutoff.tzinfo is None: raise ValueError('Timezone required')
        if any(datetime.fromisoformat(t['event_time']) > cutoff for t in snapshot['transactions']):
            raise ValueError('Future transaction')
        latest = {}
        for doc in snapshot['documents']:
            if datetime.fromisoformat(doc['available_at']) > cutoff: continue
            old = latest.get(doc['identity'])
            if old is None or doc['version'] > old['version']: latest[doc['identity']] = doc
        self.documents = {d['id']: d for d in latest.values()}

    def call(self, name, arguments):
        validate(arguments, INPUTS[name])
        s = self.snapshot
        if name == 'tx_overview':
            rows = s['transactions']
            data = dict(count=len(rows), total_cents=sum(r['amount_cents'] for r in rows),
                        repeated_authorization=len({r['authorization'] for r in rows}) < len(rows),
                        history_complete=s['history_complete'], baseline_daily_cents=s['baseline_daily_cents'])
            source = 'transactions'
        elif name == 'tx_reconcile':
            data, source = s['reconciliation'], 'settlement'
        elif name == 'docs_search':
            data = dict(kind=arguments['kind'], hits=[{k:d[k] for k in ('id','title','version','available_at')}
                        for d in self.documents.values() if d['kind'] == arguments['kind']])
            source = 'catalog'
        else:
            doc = self.documents.get(arguments['document_id'])
            if doc is None: raise ValueError('Document unavailable within bound case/time')
            data, source = doc, doc['kind']
        result = dict(case_id=s['case_id'], cutoff=s['cutoff'], data_kind=s['data_kind'], source=source, data=data)
        validate(result, OUTPUT)
        return result


def make_server(kind, source):
    server = Server(f'riskcase-{kind}', version='1.0.0')
    names = [n for n in INPUTS if n.startswith('tx_' if kind == 'transactions' else 'docs_')]

    @server.list_tools()
    async def list_tools():
        return [Tool(name=n, description=DESCRIPTIONS[n], inputSchema=INPUTS[n], outputSchema=OUTPUT) for n in names]

    @server.call_tool()
    async def call_tool(name, arguments):
        if name not in names: raise ValueError('Capability denied')
        result = source.call(name, arguments)
        return CallToolResult(content=[TextContent(type='text', text=json.dumps(result, ensure_ascii=False))], structuredContent=result)
    return server


async def serve(args):
    snapshot = json.loads(args.snapshot.read_text(encoding='utf-8'))
    if digest(snapshot) != args.sha256: raise ValueError('Snapshot fingerprint changed')
    server = make_server(args.kind, BoundSource(snapshot))
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kind', choices=['transactions','documents'], required=True)
    parser.add_argument('--snapshot', type=Path, required=True)
    parser.add_argument('--sha256', required=True)
    asyncio.run(serve(parser.parse_args()))


if __name__ == '__main__': main()
