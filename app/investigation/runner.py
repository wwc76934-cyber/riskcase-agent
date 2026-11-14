"""Bounded model loop, auditable outputs, and separate host-side scoring."""
import asyncio
from copy import deepcopy
import json
from jsonschema import validate, ValidationError
from app.agent.react_agent import Agent
from app.agent.structured_output import parse_model_json
from app.investigation.contracts import ANSWER, PROMPT
from app.investigation.verifier import supported_verdict


def verify(answer, evidence):
    validate(answer, ANSWER)
    if any(e not in evidence for e in answer['evidence_ids']): raise ValueError('Unknown evidence')
    sources = {evidence[e]['result']['source'] for e in answer['evidence_ids']}
    if not {'transactions','procedure'} <= sources: raise ValueError('Missing core originals')
    if answer['verdict'] == 'insufficient_evidence' and not answer['missing']: raise ValueError('Missing gap explanation')
    if answer['verdict'] != 'insufficient_evidence' and answer['missing']: raise ValueError('Unresolved gaps')
    return supported_verdict(answer,evidence)


async def run(client, gateway, question, *, save):
    messages = [dict(role='system',content=PROMPT), dict(role='user',content=question)]
    result = dict(status='running', answer=None, messages=messages, real_model=not getattr(client,'simulation',False))
    try:
        for turn in range(6):
            response = await asyncio.to_thread(client.chat, deepcopy(messages), tools=gateway.schemas)
            # Save raw before protocol or answer parsing, including every failure.
            result['last_raw_response'] = response
            save(result | dict(trace=gateway.trace, evidence=gateway.evidence))
            Agent._validate_assistant_message(response)
            messages.append(response)
            calls = response.get('tool_calls') or []
            if calls:
                if len(calls) > 12: raise ValueError('Oversized action batch')
                for call in calls:
                    fn = call['function']
                    observation = await gateway.call(fn['name'], fn['arguments'])
                    messages.append(dict(role='tool',tool_call_id=call['id'], content=json.dumps(observation,ensure_ascii=False)))
                save(result | dict(trace=gateway.trace,evidence=gateway.evidence))
                continue
            try:
                answer, output_mode = parse_model_json(response['content'])
                validate(answer, ANSWER)
            except (ValueError, ValidationError):
                if turn >= 5: raise
                messages.append(dict(role='user',content='Formatting/schema correction only. Preserve your conclusion and factual content. Return exactly the four-key JSON contract, no prose or Markdown. No tools allowed. Do not reinterpret evidence.'))
                correction = await asyncio.to_thread(client.chat, deepcopy(messages), tools=[])
                result['format_repair_used'] = True
                result['repair_raw_response'] = correction
                save(result | dict(trace=gateway.trace,evidence=gateway.evidence))
                Agent._validate_assistant_message(correction)
                messages.append(correction)
                if correction.get('tool_calls'): raise ValueError('Repair cannot call tools')
                answer, output_mode = parse_model_json(correction['content'])
                validate(answer, ANSWER)
            result['proposed_answer']=answer
            validation=verify(answer, gateway.evidence)
            result.update(status='completed', answer=answer, output_mode=output_mode, validation=validation)
            break
        else: result['status'] = 'iteration_limit'
    except Exception as exc:
        result.update(status='failed', error_type=type(exc).__name__)
    result.update(trace=gateway.trace, evidence=gateway.evidence)
    save(result)
    return result


def score(result, gold):
    answer = result.get('answer') or {}
    cited = [result['evidence'][e]['result'] for e in answer.get('evidence_ids',[]) if e in result['evidence']]
    sources = {r['source'] for r in cited}
    # An empty, successful disposition search proves a missing record, not a disposition fact.
    missing_disposition = any(r['source']=='catalog' and r['data'].get('kind')=='disposition' and not r['data']['hits'] for r in cited)
    if missing_disposition: sources.add('disposition')
    verdict_ok = answer.get('verdict') == gold['verdict']
    coverage_ok = set(gold['required_sources']) <= sources
    return dict(verdict_correct=verdict_ok, required_sources_cited=coverage_ok,
                task_passed=result['status']=='completed' and verdict_ok and coverage_ok,
                tool_attempts=len(result['trace']), denied_attempts=sum(not e['ok'] for e in result['trace']),
                semantic_text_review='not_automatically_certified')


class ScriptedInvestigator:
    """Evidence-driven deterministic control, explicitly NOT an LLM."""
    simulation = True

    def __init__(self, *, exhaustive=False): self.exhaustive = exhaustive

    def chat(self, messages, tools=None):
        observations = [json.loads(m['content']) for m in messages if m['role']=='tool']
        successful = [o for o in observations if o.get('ok')]
        def by_source(source): return next((o for o in successful if o['source']==source), None)
        overview, procedure = by_source('transactions'), by_source('procedure')
        searches = {o['data']['kind']:o for o in successful if o['source']=='catalog'}
        calls = []
        def add(name, **args):
            calls.append(dict(id=f'call-{len(observations)}-{len(calls)}',type='function',function=dict(name=name,arguments=json.dumps(args))))
        def obtain(kind):
            if kind not in searches: add('docs_search',kind=kind)
            elif searches[kind]['data']['hits'] and by_source(kind) is None:
                add('docs_read',document_id=searches[kind]['data']['hits'][0]['id'])
        if overview is None: add('tx_overview')
        if procedure is None: obtain('procedure')
        if self.exhaustive:
            for kind in ('merchant','disposition'): obtain(kind)
            if not by_source('settlement'): add('tx_reconcile')
        elif overview and overview['data']['history_complete']:
            if overview['data']['repeated_authorization']:
                if not by_source('settlement'): add('tx_reconcile')
            else:
                obtain('merchant'); obtain('disposition')
        if calls: return dict(role='assistant', content=None, tool_calls=calls)
        cited = [overview['evidence_id'],procedure['evidence_id']]
        missing = []
        if not overview['data']['history_complete']:
            verdict, missing = 'insufficient_evidence', ['Complete transaction history']
        elif overview['data']['repeated_authorization']:
            rec = by_source('settlement'); cited.append(rec['evidence_id'])
            if not rec['data']['complete']: verdict,missing = 'insufficient_evidence',['Settlement reconciliation']
            else: verdict = 'duplicate_record' if rec['data']['settled_debits']==1 else 'needs_review'
        else:
            merchant, disposition = by_source('merchant'), by_source('disposition')
            cited.append(merchant['evidence_id'])
            if disposition:
                cited.append(disposition['evidence_id'])
                verdict = 'normal_change' if disposition['data']['status']=='confirmed' else 'needs_review'
            else:
                cited.append(searches['disposition']['evidence_id'])
                verdict, missing = 'insufficient_evidence', ['Independent disposition record']
        return dict(role='assistant', content=json.dumps(dict(verdict=verdict,evidence_ids=cited,
                    summary='Deterministic control follows synthetic research policy; no fraud verdict.',missing=missing)))
