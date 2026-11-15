from copy import deepcopy
import pytest
from jsonschema import Draft202012Validator
from tests.test_v3_reliability import acquired
from tests.test_fraud_inquiry import inquiry_snapshot
from tests.test_v32_report_plan import plan
from app.investigation.v3.selected_fact_report import SelectedFactReportContract
from app.investigation.v3.output import OutputFailure

def candidate(acquired):
    ledger,_,e,p=plan(acquired);c=SelectedFactReportContract(ledger.target_id)
    p['source_statement'].pop('value');p['source_statement'].pop('fact_ids')
    p['source_statement']['selected_fact_id']='F1'
    return ledger,c,e,p

def test_host_copies_only_the_explicitly_selected_model_fact(acquired):
    ledger,c,e,p=candidate(acquired);before=deepcopy(e)
    result=c.assemble(p,e,ledger)
    assert result['source_statement']['value']==e['facts']['F1']['value']=='denied'
    assert result['source_statement']['fact_ids']==['F1'] and e==before
    assert result['model_selected_customer_fact_id']=='F1'
    assert not result['semantic_truth_validated'] and not result['disposition_executed']

@pytest.mark.parametrize('bad',['V1','C1','F999'])
def test_model_cannot_select_an_event_source_handle_or_unread_fact(acquired,bad):
    ledger,c,e,p=candidate(acquired);p['source_statement']['selected_fact_id']=bad
    with pytest.raises(OutputFailure,match='model_schema'):c.assemble(p,e,ledger)

def test_null_selection_remains_unknown_without_host_fact_completion(acquired):
    ledger,c,e,p=candidate(acquired);p['source_statement']['selected_fact_id']=None
    result=c.assemble(p,e,ledger)
    assert result['source_statement']['value']=='unresolved' and result['source_statement']['fact_ids']==[]
    assert result['facts']==list(e['facts'].values())  # No silent deletion of contrary observations.

def test_supported_and_weakened_status_require_their_selected_evidence_in_schema(acquired):
    ledger,c,e,p=candidate(acquired)
    for status,field in [('supported','support'),('weakened','counterevidence')]:
        trial=deepcopy(p);h=trial['investigation']['hypotheses'][0]
        h.update(status=status,support=[],counterevidence=[])
        assert not Draft202012Validator(c.schema_for(e,ledger)).is_valid(trial)
        h[field]=['F1'];assert Draft202012Validator(c.schema_for(e,ledger)).is_valid(trial)

def test_authentication_cannot_supply_customer_authorization(acquired):
    ledger,c,_,p=candidate(acquired)
    handle=next(k for k,v in ledger.lines.items() if v['source_type']=='authentication')
    e=c.extract(dict(facts=[dict(predicate='authentication',value='success',refs=[handle])]),ledger)
    assert not c.eligible(e)
    with pytest.raises(OutputFailure):c.assemble(p,e,ledger)

def test_semantically_wrong_model_extraction_is_not_corrected_from_original_or_reference(acquired):
    ledger,c,_,p=candidate(acquired)
    from tests.test_v3_typed_review import payloads
    _,facts,_=payloads(acquired);facts['facts'][0]['value']='authorized'
    e=c.extract(facts,ledger);result=c.assemble(p,e,ledger)
    assert result['source_statement']['value']=='authorized' and not result['semantic_truth_validated']
    # This is intentionally not a correctness test: independent business scoring
    # must penalize wrong interpretations instead of the host fixing the model.

def test_acquired_customer_original_does_not_fill_an_omitted_model_observation(acquired):
    ledger,c,_,p=candidate(acquired);e=c.extract(dict(facts=[]),ledger)
    p['source_statement']['selected_fact_id']=None
    for h in p['investigation']['hypotheses']:h.update(status='open',support=[],counterevidence=[])
    p['investigation']['recommendations']=[dict(action_code='A1',fact_ids=[],reason='Review missing observations')]
    p['investigation']['tensions']=[]
    result=c.assemble(p,e,ledger)
    assert result['facts']==[] and result['source_statement']['value']=='unresolved'
    assert any(line['source_type']=='customer' for line in ledger.lines.values())

def test_other_payment_observation_cannot_supply_target_source_position(acquired):
    ledger,c,e,p=candidate(acquired)
    e=deepcopy(e);e['facts']['F1']['transaction_ids']=['another-payment']
    with pytest.raises(OutputFailure):c.assemble(p,e,ledger)

def test_selected_fact_copy_does_not_bypass_extraction_integrity(acquired):
    ledger,c,e,p=candidate(acquired);e=deepcopy(e);e['hash']='tampered'
    with pytest.raises(OutputFailure,match='extraction_changed'):c.assemble(p,e,ledger)
