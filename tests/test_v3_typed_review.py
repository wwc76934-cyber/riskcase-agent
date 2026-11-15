from copy import deepcopy
import json
import pytest
from jsonschema import Draft202012Validator
from tests.test_fraud_inquiry import inquiry_snapshot
from tests.test_v3_reliability import acquired,Client,job
from app.investigation.v3.typed_review import TypedReviewContract
from app.investigation.v3.output import PREDICATE_ROLES,OutputFailure
from app.investigation.v3.delivery import StagedDelivery


def payloads(acquired):
    ledger,_,_,decision=acquired;target=ledger.target_id
    customer=next(k for k,v in ledger.lines.items() if v['source_type'] in PREDICATE_ROLES['customer_authorization'] and target in v['transaction_ids'])
    facts=dict(facts=[dict(predicate='customer_authorization',value='denied',refs=[customer])])
    report=dict(investigation=deepcopy(decision),source_statement=dict(value='denied',fact_ids=['F1'],rationale='Scripted source-state fixture, not confirmed fraud'),proposed_disposition='continue_collection')
    report['investigation'].pop('fact_ids')
    return ledger,facts,report


def test_direct_fact_types_exclude_procedure_text_and_declare_customer_states(acquired):
    ledger,facts,_=payloads(acquired);contract=TypedReviewContract(ledger.target_id)
    validator=Draft202012Validator(contract.extraction_schema(ledger));assert validator.is_valid(facts)
    bad=deepcopy(facts);bad['facts'][0]['predicate']='policy_version';bad['facts'][0]['value']='Policy prose '*30
    assert not validator.is_valid(bad)
    bad=deepcopy(facts);bad['facts'][0]['value']='not_authorized_with_a_free_explanation'
    assert not validator.is_valid(bad)
    assert 'id' not in contract.extraction_schema(ledger)['properties']['facts']['items']['anyOf'][0]['properties']


def test_nonempty_typed_delivery_assembles_metadata_without_interpreting_it(tmp_path,acquired):
    ledger,facts,report=payloads(acquired);contract=TypedReviewContract(ledger.target_id)
    store,identity,claim=job(tmp_path);client=Client([facts,report])
    result=StagedDelivery(store,claim,ledger,client,max_requests=2,report_contract=contract).run()
    assert client.calls==2 and result['source_statement']['transaction_id']==ledger.target_id
    assert result['source_statement']['value']=='denied' and result['facts'][0]['id']=='F1'
    assert result['facts'][0]['citations'][0]['text']==ledger.lines[facts['facts'][0]['refs'][0]]['text']
    assert result['human_review_required'] and not result['semantic_truth_validated']
    assert 'confirmed_case_outcome' in result['missing']


def test_procedure_originals_stay_available_once_in_report_context(acquired):
    ledger,facts,_=payloads(acquired);contract=TypedReviewContract(ledger.target_id);extraction=contract.extract(facts,ledger)
    context=contract.context_for(extraction,ledger)
    assert context['acquired']['read_line_handles']==ledger.lines
    assert 'citations' not in context['observations']['F1']
    assert context['observations']['F1']['source_handles']==facts['facts'][0]['refs']
    procedures=[v for v in context['acquired']['read_line_handles'].values() if v['source_type'] in PREDICATE_ROLES['policy_version']]
    assert procedures and all(v['text'] for v in procedures)


def test_missing_model_customer_fact_is_not_filled_from_an_acquired_original(tmp_path,acquired):
    ledger,_,report=payloads(acquired);contract=TypedReviewContract(ledger.target_id)
    store,identity,claim=job(tmp_path);client=Client([dict(facts=[]),report])
    with pytest.raises(OutputFailure,match='model_schema'):
        StagedDelivery(store,claim,ledger,client,max_requests=2,report_contract=contract).run()
    assert store.load(identity)['extraction']['facts']=={} and client.calls==2


def test_event_id_cannot_be_used_as_a_fact_reference(acquired):
    ledger,facts,report=payloads(acquired);contract=TypedReviewContract(ledger.target_id);extraction=contract.extract(facts,ledger)
    report['investigation']['recommendations'][0]['fact_ids']=[next(iter(ledger.events))]
    with pytest.raises(OutputFailure,match='model_schema'):contract.assemble(report,extraction,ledger)


def test_typed_response_recovery_reuses_the_saved_model_call(tmp_path,acquired):
    ledger,facts,report=payloads(acquired);contract=TypedReviewContract(ledger.target_id)
    now=[100];store,identity,claim=job(tmp_path,lambda:now[0]);first=Client([facts])
    def crash(kind,ordinal):
        if kind=='response_saved':raise SystemExit('Scripted process interruption')
    with pytest.raises(SystemExit):
        StagedDelivery(store,claim,ledger,first,max_requests=2,report_contract=contract,lease_seconds=30,hook=crash).run()
    now[0]=131;replacement=store.claim('replacement',lease_seconds=30,deadline_seconds=180,mode='offline');second=Client([report])
    result=StagedDelivery(store,replacement,ledger,second,max_requests=2,report_contract=contract,lease_seconds=30).run()
    assert first.calls==second.calls==1 and store.load(identity)['replayed_responses']==1
    assert result['facts'][0]['id']=='F1' and result['source_statement']['value']=='denied'
