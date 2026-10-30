"""Frozen case executor; scripted controls and live clients remain explicitly separate."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import time
from app.investigation.gateway import connect,Gateway
from app.investigation.runner import run,ScriptedInvestigator
from app.investigation.harness import InvestigationService,HarnessWorker,SkillScriptedClient
from app.evaluation.adapters import local_connect
from app.evaluation.scoring import score
from app.service.runtime import write_json
from app.investigation.recording import RecordedClient

ARMS=['fixed_code','basic_agent','full_agent','no_skill','no_knowledge']


def execute(case,arm,directory,*,knowledge,live_factory=None,real_mcp=False):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=False)
    live=live_factory is not None
    if live and (arm=='fixed_code' or not real_mcp):raise ValueError('Live evaluation requires model arm and real MCP')
    factory=connect if real_mcp else local_connect
    base=live_factory() if live else ScriptedInvestigator(exhaustive=arm=='fixed_code') if arm in ('fixed_code','basic_agent') else SkillScriptedClient()
    client=RecordedClient(base,directory);start=time.monotonic()
    result={};state=None;job=None
    try:
        if arm in ('fixed_code','basic_agent'):
            async def basic():
                snapshot=directory/'snapshot.json';write_json(snapshot,case['snapshot'])
                with (directory/'mcp-stderr.log').open('w',encoding='utf-8') as errlog:
                    async with factory(snapshot,case['snapshot'],errlog=errlog) as (sessions,handshakes):
                        write_json(directory/'handshakes.json',handshakes)
                        gateway=Gateway(sessions,case['snapshot'],audit_path=directory/'tool-events.jsonl')
                        return await run(client,gateway,case['question'],save=lambda r:write_json(directory/'result.json',client.scrub(r)))
            result=asyncio.run(basic());attempts=len(result.get('trace',[]));host_attempts=0
        else:
            root=Path(__file__).resolve().parents[2]
            service=InvestigationService(directory/'harness',root/'investigation_skills',
                profile='full' if arm=='full_agent' else arm,execution_mode='live' if live else 'offline',knowledge_snapshot=knowledge)
            job,_=service.submit('evaluation-'+case['id'],case['snapshot'],case['question'])
            row=HarnessWorker(service,client_factory=lambda:client,connection_factory=factory).step()
            state=service.store.load(job) or {};attempts=state.get('attempts',0)
            host_attempts=attempts-len(state.get('trace',[]))
            result=dict(status='completed_validated' if row['state']=='awaiting_review' else row['state'],reason=row['reason'],
                answer=state.get('answer'),proposed_answer=state.get('proposed_answer'),evidence=state.get('evidence',{}),trace=state.get('trace',[]),
                raw=state.get('raw'),turns=state.get('turn',0),job=job,generation=row['generation'],features=service.profile,
                active_skills=list(state.get('skills',{})),knowledge_queries=sum(o.get('kind')=='knowledge' for o in state.get('observations',[])))
            write_json(directory/'checkpoint.json',state)
    except Exception as exc:
        if (directory/'result.json').exists():result=json.loads((directory/'result.json').read_text(encoding='utf-8'))
        result.update(status='execution_failed',error_type=type(exc).__name__)
        attempts=len(result.get('trace',[]));host_attempts=0
    finally:
        client.close();audit=client.snapshot();write_json(directory/'usage.json',audit)
    write_json(directory/'result.json',client.scrub(result))
    record=dict(case=case['id'],family=case['family'],split=case['split'],arm=arm,
        client_kind='real_LLM' if live else 'scripted_control_NOT_MODEL',transport='real_MCP_stdio' if real_mcp else 'local_contract_adapter_NOT_MCP',
        score=score(result,case['gold']),status=result['status'],reason=result.get('reason',result.get('error_type')),
        tool_attempts=attempts,fact_tool_attempts=len(result.get('trace',[])),host_tool_attempts=host_attempts,
        denied_fact_calls=sum(not row['ok'] for row in result.get('trace',[])),client_calls=client.calls,
        real_requests=audit.get('requests_sent',0),elapsed_seconds=round(time.monotonic()-start,3),
        estimated_cny=audit.get('ledger',{}).get('run_committed_estimate_cny','0'),
        unknown_cost_requests=audit.get('ledger',{}).get('unknown_cost_requests',0),last_client_error=audit.get('last_error_code'),
        active_skills=result.get('active_skills',[]),knowledge_queries=result.get('knowledge_queries',0))
    write_json(directory/'score.json',record)
    return record
