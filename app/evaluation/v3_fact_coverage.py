"""Read-only supplementary coverage diagnosis, never a replacement score."""
from app.investigation.v3.output import AcquiredEvidence


def diagnose(snapshot, reference, stage, evidence, report):
    if reference.get('reference_schema') != 'v3-author-reference-v2' or reference['case_id'] != snapshot['case_id']:
        raise ValueError('Version-bound author reference required')
    target=snapshot['alert']['transaction_id']
    ledger=AcquiredEvidence(case_id=snapshot['case_id'],cutoff=snapshot['cutoff'],as_of=snapshot['stage_times'][stage],
        subject_id=snapshot['subject_id'],transaction_ids=[r['transaction_id'] for r in snapshot['transactions']],target_id=target).ingest(evidence)
    truth=reference['stages'][stage]
    if not {line['source_id'] for line in ledger.lines.values()} <= set(truth['allowed_sources']):
        raise ValueError('Unavailable source in diagnostic input')
    expected={(f['source_id'],f['predicate'],f['transaction_id']):f['value'] for f in truth['expected_observations']}
    result=[]
    for fact in report.get('facts',[]) if report else []:
        cites=fact.get('citations',[])
        structural=bool(cites) and all(c.get('handle') in ledger.lines and c==dict(handle=c['handle'],**ledger.lines[c['handle']]) for c in cites)
        if not structural:
            status='invalid_source_reference'
        elif fact.get('transaction_ids') != [target]:
            status='wrong_target_scope'
        elif fact.get('predicate') == 'policy_version':
            status=('source_metadata_matches' if type(fact.get('value')) is int
                and all(c['source_type']=='procedure' and c['version']==fact['value'] for c in cites)
                else 'source_metadata_mismatch')
        else:
            keys=[(c['source_id'],fact.get('predicate'),target) for c in cites]
            if any(k in expected and expected[k] != fact.get('value') for k in keys):
                status='contradicts_author_reference'
            elif all(k in expected for k in keys):
                status='matches_author_reference'
            else:
                status='not_covered_by_author_reference'
        result.append(dict(fact_id=fact.get('id'),predicate=fact.get('predicate'),status=status))
    return dict(diagnostic_only=True,replaces_original_score=False,delivered=report is not None,facts=result,
        all_assertions_semantically_verified=False,author_reference_not_external_truth=True,
        note='Metadata agreement is not model language understanding; uncovered facts are neither accepted nor automatically declared false.')
