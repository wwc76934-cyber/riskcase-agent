"""Raw-case scoring after execution, with no model judge or hidden answer repair."""
from copy import deepcopy
from contextlib import asynccontextmanager
import json
from pathlib import Path
import time

from app.evaluation.engine import RecordedClient
from app.investigation.dossier import BoundSource, INPUTS, verify
from mcp.types import CallToolResult
from app.investigation.gateway import connect
from app.investigation.harness import InvestigationService, HarnessWorker
from app.service.runtime import write_json


@asynccontextmanager
async def local_connect(path, snapshot, *, errlog):
    class Session:
        async def call_tool(self, name, args):
            return CallToolResult(content=[], structuredContent=BoundSource(snapshot).call(name, args))
    session = Session()
    yield {name: session for name in INPUTS}, {'transport': 'local_contract_adapter_NOT_MCP'}


def score(answer, evidence, gold, *, finished, purchase_cents=None):
    structural = False
    try:
        verify(answer, evidence,purchase_cents=purchase_cents,support_contract=purchase_cents is not None);structural = True
    except Exception:
        # Evaluation failures are recorded as failures, never removed from denominator.
        pass
    claims = {c['name']: c for c in (answer or {}).get('claims', [])}
    fields = {}
    for name, expected in gold['values'].items():
        claim = claims.get(name, {})
        covered = set()
        for ref in claim.get('citations', []):
            record = evidence.get(ref.get('evidence_id'), {}).get('result', {})
            if record.get('source') != 'raw_document':continue
            for line in range(ref.get('line_start', 0), ref.get('line_end', -1)+1):
                covered.add((record['data']['id'], line))
        fields[name] = dict(value_correct=claim.get('value') == expected,
                           required_source_lines=all(tuple(r) in covered for r in gold['required_lines'][name]))
    policy_ok = False
    try:
        ref = answer['policy'];result = evidence[ref['evidence_id']]['result']
        policy_ok = (result['source']=='policy' and result['data']['documents'][0]['id']==gold['policy_id']
                     and all(ref['line_start']<=line<=ref['line_end'] for line in gold['policy_lines']))
    except (KeyError, TypeError, IndexError):pass
    decision = (answer or {}).get('verdict') == gold['verdict']
    missing = sorted((answer or {}).get('missing', [])) == sorted(gold['missing'])
    grounded = sum(v['value_correct'] and v['required_source_lines'] for v in fields.values())
    return dict(task_passed=bool(finished and structural and decision and policy_ok and missing and grounded==4),
                decision_correct=decision, values_correct=sum(v['value_correct'] for v in fields.values()),
                grounded_claims=grounded, total_claims=4, citations_valid=structural,
                policy_correct=policy_ok, gaps_correct=missing, fields=fields,
                grading='author_annotated_values_and_required_spans_NOT_external_truth')


class FixtureClient:
    """Gold-assisted contract fixture, deliberately NOT a model or performance baseline."""
    simulation = True

    def __init__(self, gold):self.gold = deepcopy(gold)

    def chat(self, messages, tools=None):
        frame = json.loads(messages[1]['content'])
        observed = frame['observations']
        bydoc = {o['data']['id']: o for o in observed if o.get('source') == 'raw_document'}
        policy = next((o for o in observed if o.get('source') == 'policy'), None)
        loaded = any(o.get('kind') == 'skill' for o in observed)
        if not loaded:
            calls = [('load_skill', {'name': 'raw-payment-investigation'}),
                     ('policy_search', {'query': 'duplicate payment refund'}),
                     ('case_search', {'query': 'clearing mapping refund'})]
        elif not all(d in bydoc for d in ('clearing', 'mapping', 'refund-ops', 'refund-ledger')):
            calls = [('case_read', {'document_id': d}) for d in ('clearing', 'mapping', 'refund-ops', 'refund-ledger') if d not in bydoc]
        else:
            claims = []
            for name, value in self.gold['values'].items():
                grouped = {}
                for doc, line in self.gold['required_lines'][name]:
                    grouped.setdefault(doc, []).append(line)
                refs = [dict(evidence_id=bydoc[d]['evidence_id'],
                             line_start=1 if d=='clearing' and name in ('settled_debits','net_debit_cents') else min(lines),
                             line_end=len(bydoc[d]['data']['lines']) if d=='clearing' and name in ('settled_debits','net_debit_cents') else max(lines)) for d, lines in grouped.items()]
                claims.append(dict(name=name, value=value, citations=refs))
            return dict(role='assistant', content=json.dumps(dict(verdict=self.gold['verdict'], claims=claims,
                policy=dict(evidence_id=policy['evidence_id'], line_start=2, line_end=5), missing=self.gold['missing'])))
        return dict(role='assistant', content=None, tool_calls=[dict(id='fixture-'+str(i),type='function',
                    function=dict(name=n,arguments=json.dumps(a))) for i,(n,a) in enumerate(calls)])


def execute(case, strategy, directory, *, live_factory=None, real_mcp=False):
    directory = Path(directory);directory.mkdir(parents=True, exist_ok=False)
    root = Path(__file__).resolve().parents[2]
    if live_factory and not real_mcp:raise ValueError('Live requires real MCP')
    base = live_factory() if live_factory else FixtureClient(case['gold'])
    client = RecordedClient(base, directory)
    service = InvestigationService(directory/'harness', root/'dossier_skills', strategy=strategy,
                                   execution_mode='live' if live_factory else 'offline')
    identity, _ = service.submit('dossier-'+case['id'], case['snapshot'], case['question'])
    start = time.monotonic()
    try:
        row = HarnessWorker(service, client_factory=lambda: client,
                            connection_factory=connect if real_mcp else local_connect).step()
    finally:
        client.close()
        write_json(directory/'usage.json', client.snapshot())
    state = service.store.load(identity) or {}
    answer = state.get('answer') or state.get('proposed_answer')
    result = dict(state=row['state'], reason=row['reason'], answer=answer, evidence=state.get('evidence', {}),
                  validation=state.get('validation'), job=identity, raw=state.get('raw'),draft_reviews=state.get('draft_reviews',[]))
    write_json(directory/'result.json', result);write_json(directory/'checkpoint.json', state)
    usage = client.snapshot()
    record = dict(case=case['id'], split=case['split'], family=case['family'], strategy=strategy,
                  client_kind='real_LLM' if live_factory else 'gold_assisted_contract_fixture_NOT_PERFORMANCE',
                  transport='real_MCP_stdio' if real_mcp else 'local_contract_adapter_NOT_MCP',
                  score=score(answer, result['evidence'], case['gold'], finished=row['state']=='awaiting_review',purchase_cents=case['snapshot'].get('purchase_cents')),
                  state=row['state'], reason=row['reason'], model_requests=usage.get('requests_sent', 0),
                  tool_attempts=state.get('attempts', 0),
                  policy_queries=sum(o.get('source')=='policy' for o in state.get('observations', [])),
                  originals_read=sum(o.get('source')=='raw_document' for o in state.get('observations', [])),
                  elapsed_seconds=round(time.monotonic()-start, 3),
                  estimated_cny=usage.get('ledger', {}).get('run_committed_estimate_cny', '0'),
                  unknown_cost_requests=usage.get('ledger', {}).get('unknown_cost_requests', 0),
                  last_client_error=usage.get('last_error_code'))
    first=state.get('draft_reviews',[{}])[0].get('answer') if state.get('draft_reviews') else answer
    record['draft_review_failures']=len(state.get('draft_reviews',[]))
    record['first_draft_score']=score(first,result['evidence'],case['gold'],finished=bool(first),purchase_cents=case['snapshot'].get('purchase_cents'))
    write_json(directory/'score.json', record)
    return record
