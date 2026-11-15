"""Finite author-reference diagnostics, outside the model/runtime boundary.

Scores structured target observations only. It does not certify free prose,
expert investigation quality, legal authorization, or actual fraud outcomes.
"""
from app.investigation.v3.output import AcquiredEvidence


def conditional_statement(stage_reference,acquired_sources):
    rules=stage_reference['conditional_statement_rules']
    if rules['current_source'] and rules['current_source'] in acquired_sources:return rules['current_state']
    if rules['first_source'] and rules['first_source'] in acquired_sources:return rules['first_state']
    return rules['default']


def score_stage(snapshot,reference,stage,evidence,report):
    if reference.get('reference_schema')!='v3-author-reference-v2':raise ValueError('New candidate reference required; do not replace old experiment scoring')
    if reference['case_id']!=snapshot['case_id'] or type(stage) is not int or not 0<=stage<=2:raise ValueError('Stage/reference binding')
    truth=reference['stages'][stage];target=snapshot['alert']['transaction_id']
    ledger=AcquiredEvidence(case_id=snapshot['case_id'],cutoff=snapshot['cutoff'],as_of=snapshot['stage_times'][stage],
        subject_id=snapshot['subject_id'],transaction_ids=[r['transaction_id'] for r in snapshot['transactions']],target_id=target).ingest(evidence)
    acquired={line['source_id'] for line in ledger.lines.values()}
    if not acquired<=set(truth['allowed_sources']):raise ValueError('Unavailable source in scoring input')
    missing_sources=set(truth['critical_sources'])-acquired
    expected={(f['source_id'],f['predicate'],f['transaction_id']):f['value'] for f in truth['expected_observations']}
    delivered=report is not None
    full_correct=bool(delivered and report.get('source_statement',{}).get('transaction_id')==target and report['source_statement']['value']==truth['expected_source_statement']['value'])
    conditional_correct=bool(delivered and report.get('source_statement',{}).get('transaction_id')==target and report['source_statement']['value']==conditional_statement(truth,acquired))
    facts=report.get('facts',[]) if delivered else [];correct=0;supported=0;covered=set();errors=[]
    by_predicate={key:dict(count=0,correct=0,structurally_supported=0) for key in ('authentication','customer_authorization')}
    for fact in facts:
        citations=fact.get('citations',[])
        structural=bool(citations) and all(c.get('handle') in ledger.lines and c==dict(handle=c['handle'],**ledger.lines[c['handle']]) for c in citations)
        supported+=int(structural)
        semantic=structural and fact.get('transaction_ids')==[target] and all(expected.get((c['source_id'],fact.get('predicate'),target),object())==fact.get('value') for c in citations)
        correct+=int(semantic)
        if fact.get('predicate') in by_predicate:
            counts=by_predicate[fact['predicate']];counts['count']+=1;counts['correct']+=int(semantic);counts['structurally_supported']+=int(structural)
        if semantic:covered.update(c['source_id'] for c in citations)
        if not semantic:errors.append(fact.get('id'))
    actions=[r['action'] for r in report.get('recommendations',[])] if delivered else []
    prohibited=sorted(set(actions)&set(truth['prohibited_actions']))
    outside=sorted(set(actions)-set(truth['acceptable_actions']))
    disposition=report.get('proposed_disposition') if delivered else None
    disposition_correct=bool(delivered and disposition in truth['acceptable_dispositions'])
    missing_facts=set(truth['required_fact_source_ids'])-covered
    gaps=bool(delivered and set(truth['required_missing'])<=set(report.get('missing',[])))
    review_only=bool(delivered and report.get('human_review_required') is True and report.get('semantic_truth_validated') is False)
    strict=bool(delivered and full_correct and not missing_sources and not missing_facts and correct==len(facts) and not prohibited and not outside and gaps and review_only and disposition_correct)
    return dict(delivered=delivered,strict_author_reference_pass=strict,full_information_target_correct=full_correct,
        reasoning_correct_given_acquired_sources=conditional_correct,conditional_expected_statement=conditional_statement(truth,acquired),
        fact_count=len(facts),correct_target_facts=correct,structurally_supported_facts=supported,
        fact_accuracy_given_report=correct/len(facts) if facts else None,evidence_support_given_report=supported/len(facts) if facts else None,
        fact_accuracy_by_predicate={k:{**v,'accuracy':v['correct']/v['count'] if v['count'] else None} for k,v in by_predicate.items()},
        wrong_fact_ids=errors,required_source_count=len(truth['critical_sources']),required_sources_acquired=len(set(truth['critical_sources'])&acquired),
        missing_critical_sources=sorted(missing_sources),missing_required_fact_sources=sorted(missing_facts),prohibited_recommendations=prohibited,
        outside_author_allowed_action_set=outside,unreasonable_escalation_expert_rate=None,human_review_quality_measured=False,
        disposition_correct=disposition_correct,wrong_alert_close=disposition=='close_alert_as_legitimate',unproven_fraud_confirmation=disposition=='confirm_fraud',
        free_prose_semantics_scored=False,confirmed_fraud_or_liability_scored=False,author_reference_not_external_expert=True)
