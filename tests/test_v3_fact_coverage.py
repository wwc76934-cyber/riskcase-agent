from copy import deepcopy
from tests.test_v3_score import fixture,report_for
from app.evaluation.v3_fact_coverage import diagnose
from app.evaluation.v3_score import score_stage


def test_metadata_and_uncovered_fact_are_not_misreported_as_verified_language_facts():
    runtime,ref,evidence,ledger=fixture(1);report=report_for(ref,1,ledger)
    original=deepcopy(report);policy=next((h,c) for h,c in ledger.lines.items() if c['source_type']=='procedure')
    report['facts'] += [dict(id='Fextra1',predicate='policy_version',value=policy[1]['version'],transaction_ids=[ledger.target_id],citations=[dict(handle=policy[0],**policy[1])]),
                        dict(id='Fextra2',predicate='customer_retraction',value='unresolved',transaction_ids=[ledger.target_id],citations=deepcopy(original['facts'][-1]['citations']))]
    before=score_stage(runtime,ref,1,evidence,report)
    result=diagnose(runtime,ref,1,evidence,report)
    statuses={f['fact_id']:f['status'] for f in result['facts']}
    assert statuses['Fextra1']=='source_metadata_matches' and statuses['Fextra2']=='not_covered_by_author_reference'
    assert not result['all_assertions_semantically_verified'] and not result['replaces_original_score']
    assert score_stage(runtime,ref,1,evidence,report)==before


def test_wrong_target_bad_reference_and_actual_reference_contradiction_still_fail():
    runtime,ref,evidence,ledger=fixture(2);r=report_for(ref,2,ledger)
    r['facts'][0]['transaction_ids']=['different-payment']
    r['facts'][1]['value']='denied'
    r['facts'].append(dict(id='Funknown',predicate='authentication',value='success',transaction_ids=[ledger.target_id],citations=[dict(handle='C999')]))
    statuses={f['fact_id']:f['status'] for f in diagnose(runtime,ref,2,evidence,r)['facts']}
    assert statuses[r['facts'][0]['id']]=='wrong_target_scope'
    assert statuses['Funknown']=='invalid_source_reference'
    assert 'contradicts_author_reference' in statuses.values()
