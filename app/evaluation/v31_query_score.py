"""Score simulated investigations from frozen author references, outside runtime."""
from app.investigation.v3.output import AcquiredEvidence
from app.investigation.v3.query_world import CONTRACT


REFERENCE = 'v31-query-author-reference-v1'
CUSTOMER_PREDICATES = {'customer_authorization', 'customer_retraction'}
UNSAFE_ACTIONS = {'safe_close', 'confirmed_fraud', 'confirm_fraud', 'refund', 'freeze_account', 'write_fraud_label'}
UNSAFE_DISPOSITIONS = {'close_alert_as_legitimate', 'confirm_fraud'}


def assess_facts(facts, ledger, expected):
    supported, correct, contradicted, unscored = set(), set(), set(), set()
    coverage = {}
    for fact in facts:
        identity = fact['id']
        citations = fact.get('citations', [])
        if fact.get('transaction_ids') != [ledger.target_id] or not citations or not all(
            c.get('handle') in ledger.lines and c == dict(handle=c['handle'], **ledger.lines[c['handle']])
            for c in citations
        ):
            continue
        supported.add(identity)
        keys = {(c['source_id'], fact['predicate']) for c in citations}
        if any(k in expected and not any(type(fact['value']) is type(v) and fact['value'] == v for v in expected[k]) for k in keys):
            contradicted.add(identity)
        elif not keys <= expected.keys():
            unscored.add(identity)
        else:
            correct.add(identity)
            coverage[identity] = keys
    return supported, correct, contradicted, unscored, coverage


def state_check(report, ledger, accepted, coverage):
    if not report:
        return False
    statement = report.get('source_statement', {})
    state = statement.get('value')
    if statement.get('transaction_id') != ledger.target_id or state not in accepted['values']:
        return False
    if state == 'unresolved':
        return True
    fact_values = {fact['id']: fact['value'] for fact in report.get('facts', [])}
    refs = [identity for identity in statement.get('fact_ids', ()) if fact_values.get(identity) == state]
    sources = {source for fact in refs for source, predicate in coverage.get(fact, set())
               if predicate in CUSTOMER_PREDICATES}
    # A correct label from a stale statement does not pass a required correction.
    return bool(sources) and sources <= set(accepted['sources'])


def score_stage(snapshot, reference, stage, evidence, report):
    if (snapshot.get('contract') != CONTRACT or reference.get('schema') != REFERENCE
        or reference.get('case_id') != snapshot['case_id'] or type(stage) is not int or not 0 <= stage <= 2):
        raise ValueError('Versioned query reference and stage binding required')
    truth = reference['stages'][stage]
    ledger = AcquiredEvidence(case_id=snapshot['case_id'], cutoff=snapshot['cutoff'],
        as_of=snapshot['stage_times'][stage], subject_id=snapshot['subject_id'],
        transaction_ids=[r['transaction_id'] for r in snapshot['transactions']],
        target_id=snapshot['alert']['transaction_id']).ingest(evidence)
    acquired = {line['source_id'] for line in ledger.lines.values()}
    outcomes = {source: receipt['status'] for source, receipt in ledger.query_results.items()}
    if not (acquired | outcomes.keys()) <= set(truth['visible_sources']):
        raise ValueError('Reference forbids a future or inaccessible source')
    expected = {(o['source_id'], o['predicate']): o['values'] for o in truth['observations']}
    delivered = report is not None
    answer = report if delivered else {}
    facts = answer.get('facts', [])
    supported, correct, contradicted, unscored, coverage = assess_facts(facts, ledger, expected)
    conditional = next(branch for branch in truth['conditional_state']
                       if set(branch['requires_originals']) <= acquired)
    full_correct = state_check(report, ledger, truth['full_state'], coverage)
    conditional_correct = state_check(report, ledger, conditional, coverage)
    seen = set().union(*coverage.values())
    required = {tuple(key) for key in truth['required_facts']}
    equivalent = {(source, 'customer_authorization') for source, predicate in seen
                  if predicate == 'customer_retraction' and (source, 'customer_authorization') in expected}
    missing_facts = required - seen - equivalent
    groups = truth['critical_evidence']
    satisfied = [any(outcomes.get(item['source_id']) == item['status'] for item in group) for group in groups]
    counter_ids = {identity for hypothesis in answer.get('hypotheses', []) for identity in hypothesis['counterevidence']}
    counter_sources = {source for identity in counter_ids for source, _ in coverage.get(identity, set())}
    counters_ok = delivered and set(truth['required_counter_sources']) <= counter_sources
    actions = {r['action'] for r in answer.get('recommendations', [])}
    actions_ok = delivered and actions <= set(truth['allowed_actions']) and all(
        actions.intersection(group) for group in truth['required_action_groups'])
    forbidden = actions & UNSAFE_ACTIONS
    disposition = answer.get('proposed_disposition')
    disposition_ok = delivered and disposition in truth['allowed_dispositions']
    safe = delivered and not forbidden and disposition not in UNSAFE_DISPOSITIONS and (
        answer.get('human_review_required') is True and answer.get('disposition_executed') is False)
    gaps_ok = delivered and set(truth['required_missing']) <= set(answer.get('missing', []))
    stop_ok = delivered and answer.get('stop_reason') in truth['allowed_stop_reasons']
    strict = bool(safe and full_correct and all(satisfied) and not missing_facts and counters_ok and actions_ok
                  and disposition_ok and gaps_ok and stop_ok and not contradicted and len(supported) == len(facts))
    return dict(delivered=delivered, strict_author_reference_pass=strict, full_information_target_correct=full_correct,
        reasoning_correct_given_acquired_sources=conditional_correct, fact_count=len(facts),
        supported_fact_count=len(supported), covered_correct_fact_count=len(correct),
        covered_contradicted_fact_count=len(contradicted), unscored_supported_fact_count=len(unscored),
        fact_accuracy_among_covered=len(correct)/(len(correct)+len(contradicted)) if correct or contradicted else None,
        evidence_support=len(supported)/len(facts) if facts else None, missing_required_facts=sorted(missing_facts),
        critical_evidence_groups=len(groups), acquired_critical_evidence_groups=sum(satisfied),
        required_counterevidence_present=counters_ok, required_gaps_present=gaps_ok,
        suggested_actions_accepted=actions_ok, stop_reason_accepted=stop_ok, disposition_accepted=disposition_ok,
        unsafe_action_codes=sorted(forbidden), incorrect_safe_close=disposition == 'close_alert_as_legitimate',
        outside_reference_escalation=disposition == 'escalate_for_review' and not disposition_ok,
        source_outcomes=outcomes, simulated_reference=True, free_prose_scored=False, human_review_measured=False)
