"""Scope/contract/transport tests for roadmap stage one; no network or model key."""
import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest
from jsonschema import ValidationError
from app.investigation.fixtures import development_cases
from app.investigation.mcp_server import BoundSource
from app.investigation.gateway import Gateway, connect
from app.investigation.runner import run, score, ScriptedInvestigator, verify
from app.investigation.contracts import digest


def test_atomic_batch_and_unknown_cost_ceiling(tmp_path):
    from app.investigation.budget import BatchLedger
    from app.llm.cost_ledger import BudgetExceeded
    ledger=BatchLedger(tmp_path/'cost.sqlite','a'*32)
    for case in ('one','two'):
        identity=ledger.reserve(ledger.prefix+case,'1',run_budget='1',project_budget='20',max_requests=6)
        ledger.settle(identity)  # Unknown cost retains full reservation.
    with pytest.raises(BudgetExceeded,match='batch_estimated_spend_limit'):
        ledger.reserve(ledger.prefix+'three','.01',run_budget='1',project_budget='20',max_requests=6)
    with pytest.raises(ValueError):
        ledger.reserve('outside-batch','.01',run_budget='1',project_budget='20',max_requests=6)


def test_batch_case_limit(tmp_path):
    from app.investigation.budget import BatchLedger
    from app.llm.cost_ledger import BudgetExceeded
    ledger=BatchLedger(tmp_path/'cost.sqlite','b'*32)
    for case in range(12):
        ledger.reserve(ledger.prefix+str(case),'.001',run_budget='1',project_budget='20',max_requests=6)
    with pytest.raises(BudgetExceeded,match='batch_case_limit'):
        ledger.reserve(ledger.prefix+'13','.001',run_budget='1',project_budget='20',max_requests=6)


class Session:
    def __init__(self,snapshot): self.source=BoundSource(snapshot)
    async def call_tool(self,name,args):
        return SimpleNamespace(isError=False,structuredContent=self.source.call(name,args))


def gateway(snapshot,**kwargs):
    return Gateway({n:Session(snapshot) for n in ('tx_overview','tx_reconcile','docs_search','docs_read')},snapshot,**kwargs)


@pytest.mark.parametrize('case',development_cases(),ids=lambda c:c['id'])
def test_branch_controls(case):
    async def scenario():
        results=[]
        for exhaustive in (False,True):
            g=gateway(case['snapshot'])
            result=await run(ScriptedInvestigator(exhaustive=exhaustive),g,case['question'],save=lambda _:None)
            assert score(result,case['gold'])['task_passed']
            assert not any(not row['ok'] for row in result['trace'])
            results.append(result)
        adaptive,full=results
        tools=[t['tool'] for t in adaptive['trace']]
        assert ('tx_reconcile' in tools) == (case['family'] in ('duplicate','distinct_debits'))
        assert len(adaptive['trace']) <= len(full['trace'])
    asyncio.run(scenario())


def test_scope_version_and_time():
    case=development_cases()[6]['snapshot']; source=BoundSource(case)
    hits=source.call('docs_search',{'kind':'merchant'})['data']['hits']
    assert len(hits)==1 and hits[0]['version']==1
    for name in (case['documents'][0]['id'],'another-case','../secret',case['documents'][-1]['id']):
        with pytest.raises(ValueError): source.call('docs_read',{'document_id':name})
    with pytest.raises(ValidationError): source.call('tx_overview',{'case_id':'another'})
    future=deepcopy(case);future['transactions'][0]['event_time']='2027-01-01T00:00:00+00:00'
    with pytest.raises(ValueError): BoundSource(future)


def test_denials_budget_and_audit(tmp_path):
    async def scenario():
        g=gateway(development_cases()[0]['snapshot'],max_attempts=2,audit_path=tmp_path/'events.jsonl')
        assert not (await g.call('shell','{}'))['ok']
        assert not (await g.call('tx_overview','{"case_id":"other"}'))['ok']
        assert (await g.call('tx_overview','{}'))['error']=='tool_budget'
        assert len(g.trace)==3 and not g.evidence
        assert len((tmp_path/'events.jsonl').read_text().splitlines())==3
    asyncio.run(scenario())


@pytest.mark.parametrize('fault',['scope','schema','oversize','timeout','disconnect','remote_error'])
def test_untrusted_service_response(fault):
    snapshot=development_cases()[0]['snapshot']
    class Fault:
        async def call_tool(self,*args):
            if fault=='timeout': await asyncio.sleep(.1)
            if fault=='disconnect': raise ConnectionError('secret should not escape')
            body=BoundSource(snapshot).call('tx_overview',{})
            if fault=='scope': body['case_id']='other'
            if fault=='schema': body['extra']='not allowed'
            if fault=='oversize': body['data']['junk']='x'*13000
            return SimpleNamespace(isError=fault=='remote_error',structuredContent=body)
    async def scenario():
        g=Gateway({'tx_overview':Fault()},snapshot,timeout=.01)
        assert not (await g.call('tx_overview','{}'))['ok']
        assert not g.evidence
    asyncio.run(scenario())


def test_failed_raw_is_preserved():
    class Bad:
        simulation=True
        def chat(self,*args,**kwargs): return {'role':'assistant','content':'not JSON'}
    saved=[]
    result=asyncio.run(run(Bad(),gateway(development_cases()[0]['snapshot']),'Investigate',save=lambda v:saved.append(deepcopy(v))))
    assert result['status']=='failed'
    assert saved[0]['last_raw_response']['content']=='not JSON'


def test_one_format_correction_without_tools():
    case=development_cases()[0]
    class Formatting(ScriptedInvestigator):
        calls=0
        final=None
        def chat(self,messages,tools=None):
            self.calls+=1
            if self.final:
                assert tools==[]
                return self.final
            value=super().chat(messages,tools)
            if not value.get('tool_calls'):
                self.final=deepcopy(value)
                value['content']='Unwanted explanation\n'+value['content']
            return value
    client=Formatting()
    result=asyncio.run(run(client,gateway(case['snapshot']),case['question'],save=lambda _:None))
    assert result['format_repair_used'] and score(result,case['gold'])['task_passed']
    assert client.calls<=6


def test_bad_reference_does_not_trigger_format_repair():
    class Invented:
        simulation=True
        calls=0
        def chat(self,*args,**kwargs):
            self.calls+=1
            return dict(role='assistant',content=json.dumps(dict(verdict='needs_review',evidence_ids=['fake','fake2'],summary='claim',missing=[])))
    client=Invented()
    result=asyncio.run(run(client,gateway(development_cases()[0]['snapshot']),'Investigate',save=lambda _:None))
    assert result['status']=='failed' and client.calls==1


def test_reference_gate_rejects_invented_evidence():
    with pytest.raises(ValueError):
        verify(dict(verdict='needs_review',evidence_ids=['E1','E2'],summary='claim',missing=[]),{})


@pytest.mark.parametrize('mutation',['override','omit_original','policy_version','invalid_count'])
def test_research_policy_gate_uses_cited_facts(mutation):
    case=development_cases()[1]
    result=asyncio.run(run(ScriptedInvestigator(),gateway(case['snapshot']),case['question'],save=lambda _:None))
    answer=deepcopy(result['answer']);evidence=deepcopy(result['evidence'])
    if mutation=='override': answer['verdict']='normal_change'
    if mutation=='omit_original': answer['evidence_ids']=[e for e in answer['evidence_ids'] if evidence[e]['result']['source']!='settlement']
    if mutation=='policy_version':
        for e in evidence.values():
            if e['result']['source']=='procedure': e['result']['data']['rule_version']='unreviewed-v2'
    if mutation=='invalid_count':
        for e in evidence.values():
            if e['result']['source']=='settlement': e['result']['data']['settled_debits']=True
    with pytest.raises(ValueError): verify(answer,evidence)


def test_real_mcp_two_processes(tmp_path):
    snapshot=development_cases()[2]['snapshot']; path=tmp_path/'snapshot.json'
    path.write_text(json.dumps(snapshot),encoding='utf-8')
    async def scenario():
        with (tmp_path/'stderr.log').open('w',encoding='utf-8') as errlog:
            async with connect(path,snapshot,errlog=errlog) as (sessions,handshakes):
                assert len(handshakes)==2
                assert all(h['protocol'] for h in handshakes.values())
                g=Gateway(sessions,snapshot)
                result=await run(ScriptedInvestigator(),g,'Investigate',save=lambda _:None)
                assert score(result,development_cases()[2]['gold'])['task_passed']
                # Server also enforces its scope when host gateway is bypassed.
                denied=await sessions['docs_read'].call_tool('docs_read',{'document_id':'other-case'})
                assert denied.isError
                denied=await sessions['tx_overview'].call_tool('tx_overview',{'case_id':'other-case'})
                assert denied.isError
    asyncio.run(scenario())


def test_replay_rejects_wrong_conclusion_without_rewriting_history(tmp_path):
    from app.cli.audit_multisource import audit
    case=development_cases()[1]
    result=asyncio.run(run(ScriptedInvestigator(),gateway(case['snapshot']),case['question'],save=lambda _:None))
    result['answer']['verdict']='normal_change'
    record=dict(case=case['id'],arm='model',score=score(result,case['gold']),result=result)
    source=tmp_path/'old';source.mkdir()
    path=source/'results.json';path.write_text(json.dumps(dict(records=[record])),encoding='utf-8')
    before=path.read_bytes()
    rows=audit([source],tmp_path/'audit')
    assert rows[0]['result']['status']=='rejected_by_replay'
    assert path.read_bytes()==before
    assert (tmp_path/'audit/source/app/investigation/verifier.py').is_file()


def test_report_escapes_untrusted_summary(tmp_path):
    from app.cli.multisource_investigation import render
    case=development_cases()[2]
    result=asyncio.run(run(ScriptedInvestigator(),gateway(case['snapshot']),case['question'],save=lambda _:None))
    result['answer']['summary']='<script>alert(1)</script>'
    render([dict(case=case['id'],arm='model',score=score(result,case['gold']),result=result)],tmp_path)
    body=(tmp_path/'report.html').read_text(encoding='utf-8')
    assert '<script>' not in body and '&lt;script&gt;' in body


def test_transport_teardown_does_not_erase_raw_evidence(tmp_path,monkeypatch):
    from contextlib import asynccontextmanager
    from app.cli import multisource_investigation as cli
    @asynccontextmanager
    async def broken_close(path,snapshot,**kwargs):
        yield gateway(snapshot).sessions, {}
        raise ConnectionError('Simulated close failure')
    monkeypatch.setattr(cli,'connect',broken_close)
    records=asyncio.run(cli.benchmark(tmp_path/'run',limit=1))
    assert all(r['result']['status']=='transport_failed' for r in records)
    assert all(r['result']['messages'] and r['result']['evidence'] and r['result']['last_raw_response'] for r in records)
