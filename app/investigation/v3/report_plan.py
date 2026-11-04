"""Unmeasured opt-in report plan: disjoint action/gap codes, bounded hypotheses.

This is a new wire contract, not a converter for invalid historical responses.
It never repairs an enum, truncates hypotheses, or supplies a semantic fact.
"""
from copy import deepcopy
from app.investigation.contracts import digest
from app.investigation.fraud_world import ACTIONS, MISSING_TYPES
from app.investigation.v3.v31_output import V31ReviewContract
from app.investigation.v3.output import DECISION, checked, OutputFailure

ACTION_CODES = {'A'+str(i+1):value for i,value in enumerate(ACTIONS)}
GAP_CODES = {'G'+str(i+1):value for i,value in enumerate(MISSING_TYPES)}
# Numeric CNY amounts are rendered only from acquired host metadata. This
# narrow protocol rule is not a general semantic/prose truth checker.
MONETARY_LITERAL = r'(?:[0-9][0-9.,]*\s*(?:CNY|cny|元|人民币)|(?:CNY|cny|人民币|[¥￥])\s*[0-9])'


class ReportPlanContract(V31ReviewContract):
    name = 'v32-coded-report-plan-v1'

    def schema_for(self, extraction, ledger):
        schema = super().schema_for(extraction, ledger)
        p = schema['properties']['investigation']['properties']
        p['hypotheses']['maxItems'] = 6
        item = p['recommendations']['items']
        branches = item.get('anyOf', [item])
        inverse = {v:k for k,v in ACTION_CODES.items()}
        for branch in branches:
            action = branch['properties'].pop('action')
            branch['required'].remove('action'); branch['required'].append('action_code')
            branch['properties']['action_code'] = (dict(const=inverse[action['const']])
                if 'const' in action else dict(enum=[inverse[v] for v in action['enum']]))
        p['missing']['items'] = dict(enum=list(GAP_CODES))
        prose = [p['hypotheses']['items']['properties']['description'],
                 p['hypotheses']['items']['properties']['unknowns']['items'],
                 p['tensions']['items']['properties']['explanation'], p['summary'],
                 schema['properties']['source_statement']['properties']['rationale']]
        prose += [b['properties']['reason'] for b in branches]
        for field in prose:
            field['not'] = dict(pattern=MONETARY_LITERAL)
        return schema

    def context_for(self, extraction, ledger):
        context = super().context_for(extraction, ledger)
        context['report_codebook'] = dict(actions=deepcopy(ACTION_CODES), missing=deepcopy(GAP_CODES))
        # Amount display is deterministic, not a model-generated business fact.
        # This contract supports explicit cents only; never infer currency scale.
        displays = []
        for row in context['acquired']['acquired_transactions']:
            if row.get('currency') == 'CNY' and type(row.get('amount_cents')) is int:
                amount = row['amount_cents']; sign = '-' if amount < 0 else ''; magnitude = abs(amount)
                displays.append(dict(transaction_id=row['transaction_id'],amount_cents=amount,
                    amount_display=sign+str(magnitude//100)+'.'+str(magnitude%100).zfill(2)+' CNY'))
        context['host_transaction_amount_display'] = displays
        return context

    def rules_for(self, stage):
        rules = super().rules_for(stage)
        if stage == 'report':
            rules += ['Recommendations use action_code from the A codebook only. Missing evidence uses G codes only. Never interchange them or invent a code. The host decodes only codes you explicitly select.',
                      'Provide one to six hypotheses; every hypothesis and its references will be preserved, never truncated. Final report prose is unverified and cannot execute a financial action.',
                      'source_statement records the qualified customer source position, not whether independent corroboration proves it true. A clear recognition or denial can be recorded while its supporting order documents remain missing; uncertainty about actual fraud is separate from ambiguity in what the customer said.',
                      'Do not write monetary amounts in model-generated prose. Refer readers to the separate host transaction amount display. Source citations remain verbatim; numeric CNY prose is rejected rather than silently edited.']
        return rules

    def assemble(self, payload, extraction, ledger):
        checked(payload, self.schema_for(extraction, ledger))
        canonical = deepcopy(payload); p = canonical['investigation']
        for step in p['recommendations']:
            step['action'] = ACTION_CODES[step.pop('action_code')]
        p['missing'] = sorted({GAP_CODES[g] for g in p['missing']} | {'confirmed_case_outcome'})
        p['fact_ids'] = list(extraction['facts'])
        # Independent materialization avoids changing the historical two-
        # hypothesis contract or silently dropping a third model hypothesis.
        schema = deepcopy(DECISION); schema['properties']['hypotheses']['maxItems'] = 6
        checked(p, schema)
        if (extraction.get('case_id') != ledger.case_id
            or extraction.get('evidence_binding') != digest(ledger.model_context())):
            raise OutputFailure('extraction_scope_changed')
        if extraction['hash'] != digest(extraction['facts']): raise OutputFailure('extraction_changed')
        facts = extraction['facts']; selected = set(p['fact_ids'])
        if not selected <= facts.keys(): raise OutputFailure('unknown_fact')
        for h in p['hypotheses']:
            if not set(h['support']+h['counterevidence']) <= selected: raise OutputFailure('hypothesis_fact_reference')
            if h['status']=='supported' and not h['support'] or h['status']=='weakened' and not h['counterevidence']:
                raise OutputFailure('hypothesis_status_without_reference')
        for action in p['recommendations']:
            if not set(action['fact_ids']) <= selected: raise OutputFailure('recommendation_fact_reference')
            if action['action']!='analyst_review' and not action['fact_ids']: raise OutputFailure('unsupported_recommendation')
        for tension in p['tensions']:
            if not tension['fact_ids'] or not set(tension['fact_ids']) <= selected: raise OutputFailure('tension_fact_reference')
        timeline=[]
        for ref in p['timeline_refs']:
            if ref not in ledger.events: raise OutputFailure('unacquired_event')
            timeline.append(dict(handle=ref,**deepcopy(ledger.events[ref])))
        statement=deepcopy(canonical['source_statement']); statement['transaction_id']=self.target_id
        if ledger.target_id != self.target_id or self.target_id not in ledger.transaction_ids: raise OutputFailure('target_scope')
        if not set(statement['fact_ids']) <= selected: raise OutputFailure('statement_fact_reference')
        if statement['value']!='unresolved' and not any(facts[i]['predicate'] in ('customer_authorization','customer_retraction')
            and facts[i]['value']==statement['value'] and self.target_id in facts[i]['transaction_ids'] for i in statement['fact_ids']):
            raise OutputFailure('statement_inconsistent_with_extraction')
        return dict(contract='v3-task-report-v1',delivery_contract=self.name,case_id=ledger.case_id,
            status='awaiting_analyst_review',facts=[deepcopy(facts[i]) for i in p['fact_ids']],
            hypotheses=deepcopy(p['hypotheses']),timeline=sorted(timeline,key=lambda r:(r['event_time'],r['source_id'])),
            tensions=deepcopy(p['tensions']),recommendations=deepcopy(p['recommendations']),missing=p['missing'],
            stop_reason=p['stop_reason'],summary=p['summary'],extraction_hash=extraction['hash'],
            semantic_truth_validated=False,human_review_required=True,source_statement=statement,
            source_statement_is_not_confirmed_fraud_or_legal_authorization=True,
            proposed_disposition=canonical['proposed_disposition'],disposition_executed=False,
            host_amount_display=self.context_for(extraction,ledger)['host_transaction_amount_display'],
            model_prose_verified=False)
