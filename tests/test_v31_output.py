from copy import deepcopy
import pytest
from jsonschema import Draft202012Validator
from tests.test_v3_reliability import acquired
from tests.test_fraud_inquiry import inquiry_snapshot
from tests.test_v3_typed_review import payloads
from app.investigation.v3.v31_output import V31ReviewContract
from app.investigation.v3.output import OutputFailure

def test_policy_version_is_only_copied_from_a_model_selected_acquired_source(acquired):
    ledger,_,_=payloads(acquired);contract=V31ReviewContract(ledger.target_id)
    handle=next(h for h,line in ledger.lines.items() if line['source_type']=='procedure' and ledger.target_id in line['transaction_ids'])
    value=dict(facts=[dict(predicate='policy_version',refs=[handle])])
    extracted=contract.extract(value,ledger)
    assert extracted['facts']['F1']['value']==ledger.lines[handle]['version']
    assert contract.extract(dict(facts=[]),ledger)['facts']=={}
    for wrong in ('procedure '*200,999,ledger.lines[handle]['version']):
        bad=deepcopy(value);bad['facts'][0]['value']=wrong
        with pytest.raises(OutputFailure):contract.extract(bad,ledger)

@pytest.mark.parametrize('wrong',['F1','V1','C999','not-a-source'])
def test_output_cannot_invent_or_mix_line_reference_namespaces(acquired,wrong):
    ledger,wire,_=payloads(acquired);wire['facts'][0]['refs']=[wrong]
    with pytest.raises(OutputFailure):V31ReviewContract(ledger.target_id).extract(wire,ledger)

def test_bad_customer_enum_and_model_supplied_transaction_are_rejected(acquired):
    ledger,wire,_=payloads(acquired);contract=V31ReviewContract(ledger.target_id)
    bad=deepcopy(wire);bad['facts'][0]['value']='not explicitly denied so authorized'
    with pytest.raises(OutputFailure):contract.extract(bad,ledger)
    bad=deepcopy(wire);bad['facts'][0]['transaction_ids']=['another-payment']
    with pytest.raises(OutputFailure):contract.extract(bad,ledger)

def test_schema_requires_support_for_non_review_recommendations(acquired):
    ledger,wire,report=payloads(acquired);contract=V31ReviewContract(ledger.target_id);extraction=contract.extract(wire,ledger)
    schema=contract.schema_for(extraction,ledger)
    report['investigation']['recommendations']=[dict(action='preserve_timeline',fact_ids=[],reason='Must cite actual facts')]
    assert not Draft202012Validator(schema).is_valid(report)
    report['investigation']['recommendations'][0]['fact_ids']=['F1']
    assert Draft202012Validator(schema).is_valid(report)

def test_lossless_conversion_never_turns_policy_prose_into_version(acquired):
    from app.evaluation.v31_saved_response_audit import convert
    ledger,_,_=payloads(acquired)
    h=next(h for h,l in ledger.lines.items() if l['source_type']=='procedure' and ledger.target_id in l['transaction_ids'])
    old=dict(facts=[dict(id='F1',predicate='policy_version',value='procedure prose '*50,transaction_ids=[ledger.target_id],refs=[h])])
    with pytest.raises(ValueError,match='cannot be converted'):convert(old,ledger)
    old['facts'][0]['value']=ledger.lines[h]['version']
    converted=convert(old,ledger)
    assert V31ReviewContract(ledger.target_id).extract(converted,ledger)['facts']['F1']['value']==old['facts'][0]['value']

def test_always_unknown_is_a_business_failure_when_author_reference_is_definite():
    from tests.test_v3_score import fixture,report_for
    from app.evaluation.v3_score import score_stage
    runtime,reference,evidence,ledger=fixture(2)
    assert reference['stages'][2]['expected_source_statement']['value']!='unresolved'
    report=report_for(reference,2,ledger,value='unresolved')
    score=score_stage(runtime,reference,2,evidence,report)
    assert score['delivered'] and not score['full_information_target_correct'] and not score['strict_author_reference_pass']
    assert report['human_review_required'] and not report.get('disposition_executed',False)
