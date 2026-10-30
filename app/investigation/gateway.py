"""Allowlisted MCP transport plus a single policy enforcement point."""
import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import timedelta
import json
import os
from pathlib import Path
import sys
import time

from jsonschema import validate
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from app.investigation.contracts import INPUTS, OUTPUT, DESCRIPTIONS, digest
from app.tools.case_tools import strict_json


@asynccontextmanager
async def connect(snapshot_path, snapshot, *, errlog):
    """All contexts enter/exit on the same task (SDK cancel-scope requirement)."""
    root = Path(__file__).resolve().parents[2]
    inputs = INPUTS
    module = 'app.investigation.mcp_server'
    groups = {kind: [n for n in INPUTS if n.startswith('tx_' if kind == 'transactions' else 'docs_')] for kind in ('transactions', 'documents')}
    if snapshot.get('task_contract') == 'raw-dossier-v1':
        from app.investigation.dossier import INPUTS as inputs, GROUPS as groups
        module = 'app.investigation.dossier_server'
    sessions, handshakes = {}, {}
    async with AsyncExitStack() as stack:
        for kind in ('transactions', 'documents'):
            params = StdioServerParameters(command=sys.executable,
                args=['-X','utf8','-B','-m',module,'--kind',kind,
                      '--snapshot',str(Path(snapshot_path).resolve()),'--sha256',digest(snapshot)],
                cwd=str(root), env={'PYTHONPATH':os.pathsep.join(str(p) for p in sys.path if p), 'PYTHONUTF8':'1'})
            read, write = await stack.enter_async_context(stdio_client(params, errlog=errlog))
            session = await stack.enter_async_context(ClientSession(read, write, read_timeout_seconds=timedelta(seconds=20)))
            init = await session.initialize()
            expected = set(groups[kind])
            found, cursor = {}, None
            for _ in range(8):
                page = await session.list_tools(cursor=cursor)
                for tool in page.tools:
                    if tool.name in found: raise ValueError('Duplicate tool registration')
                    found[tool.name] = tool
                cursor = page.nextCursor
                if cursor is None: break
            else: raise ValueError('Tool catalog pagination limit')
            if set(found) != expected: raise ValueError('Unexpected capability catalog')
            for name, tool in found.items():
                if tool.inputSchema != inputs[name] or tool.outputSchema != OUTPUT:
                    raise ValueError('Tool contract drift')
                sessions[name] = session
            handshakes[kind] = dict(protocol=init.protocolVersion, server=init.serverInfo.model_dump(),
                                    tools=sorted(found), schema_sha256=digest({n:inputs[n] for n in sorted(found)}))
        yield sessions, handshakes


class Gateway:
    def __init__(self, sessions, snapshot, *, max_attempts=12, timeout=15, audit_path=None):
        if type(max_attempts) is not int or not 1 <= max_attempts <= 20: raise ValueError('Invalid tool budget')
        self.sessions, self.snapshot = sessions, snapshot
        self.max_attempts, self.timeout, self.audit_path = max_attempts, timeout, audit_path
        self.trace, self.evidence = [], {}
        self.inputs, self.descriptions = INPUTS, DESCRIPTIONS
        self.sources = {'tx_overview':['transactions'], 'tx_reconcile':['settlement'],
                        'docs_search':['catalog'], 'docs_read':['procedure','merchant','disposition']}
        if snapshot.get('task_contract') == 'raw-dossier-v1':
            from app.investigation.dossier import INPUTS as inputs, DESCRIPTIONS as descriptions, SOURCES as sources
            self.inputs, self.descriptions, self.sources = inputs, descriptions, sources

    @property
    def schemas(self):
        return [dict(type='function', function=dict(name=n, description=self.descriptions[n], parameters=self.inputs[n])) for n in self.inputs]

    async def call(self, name, arguments):
        started = time.monotonic()
        entry = dict(attempt=len(self.trace)+1, tool=name, arguments=arguments, policy='readonly-v1',
                     snapshot_sha256=digest(self.snapshot), retries=0)
        self.trace.append(entry)  # Denied attempts count too.
        phase = 'policy'
        try:
            if len(self.trace) > self.max_attempts: raise ValueError('tool_budget')
            if name not in self.inputs or name not in self.sessions: raise PermissionError('capability_denied')
            phase = 'input'
            args = strict_json(arguments)
            validate(args, self.inputs[name])
            phase = 'execute'
            response = await asyncio.wait_for(self.sessions[name].call_tool(name, args), timeout=self.timeout)
            if response.isError: raise ValueError('source_error')
            phase = 'output'
            result = response.structuredContent
            validate(result, OUTPUT)
            if len(json.dumps(result, ensure_ascii=False).encode()) > 12000: raise ValueError('result_limit')
            if result['case_id'] != self.snapshot['case_id'] or result['cutoff'] != self.snapshot['cutoff']:
                raise PermissionError('source_scope_mismatch')
            expected_source = self.sources[name]
            if result['source'] not in expected_source: raise ValueError('source_contract')
            identity = f'E{len(self.evidence)+1}'
            self.evidence[identity] = dict(result=result, tool=name, arguments=args, sha256=digest(result))
            value = dict(ok=True, evidence_id=identity, **result)
            entry.update(ok=True, evidence_id=identity, result_sha256=digest(result))
        except Exception as exc:
            # Never reflect arbitrary remote text/errors (may contain credentials or injections).
            category = ('permission_denied' if isinstance(exc, PermissionError) else
                        'timeout' if isinstance(exc, TimeoutError) else
                        'tool_budget' if str(exc) == 'tool_budget' else
                        'invalid_arguments' if phase == 'input' else
                        'source_unavailable' if phase == 'execute' else
                        'invalid_result')
            value = dict(ok=False, error=category)
            entry.update(ok=False, error=category, error_type=type(exc).__name__, phase=phase)
        finally:
            entry['elapsed_ms'] = round((time.monotonic()-started)*1000, 2)
            if self.audit_path:
                with Path(self.audit_path).open('a', encoding='utf-8') as output:
                    output.write(json.dumps(entry, ensure_ascii=False)+'\n')
        return value
