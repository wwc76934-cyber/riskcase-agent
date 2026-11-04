from copy import deepcopy
import pytest
from tests.test_v3_reliability import acquired
from tests.test_fraud_inquiry import inquiry_snapshot
from tests.test_v3_typed_review import payloads
from app.investigation.v3.report_plan import ReportPlanContract,ACTION_CODES,GAP_CODES
from app.investigation.v3.v31_output import V31ReviewContract
from app.investigation.v3.output import OutputFailure


def plan(acquired):
    ledger,facts,p=payloads(acquired);c=ReportPlanContract(ledger.target_id)
    for a in p['investigation']['recommendations']:
        action=a.pop('action')
        a['action_code']=next(k for k,v in ACTION_CODES.items() if v==action)
    p['investigation']['missing']=[next(k for k,v in GAP_CODES.items() if v==s) for s in p['investigation']['missing']]
    return ledger,c,c.extract(facts,ledger),p


def test_all_model_hypotheses_are_preserved_without_relaxing_old_contract(acquired):
    ledger,c,e,p=plan(acquired);p['investigation']['hypotheses']=[deepcopy(p['investigation']['hypotheses'][0]) for _ in range(3)]
    for i,h in enumerate(p['investigation']['hypotheses']):h['description']='Distinct hypothesis '+str(i)
    r=c.assemble(p,e,ledger)
    assert len(r['hypotheses'])==3 and r['hypotheses']==p['investigation']['hypotheses']
    assert r['facts']==list(e['facts'].values()) and not r['disposition_executed'] and not r['model_prose_verified']
    old=V31ReviewContract(ledger.target_id).schema_for(e,ledger)
    assert old['properties']['investigation']['properties']['hypotheses']['maxItems']==2


@pytest.mark.parametrize('bad',['G1','related_transactions','A999'])
def test_invalid_action_or_cross_namespace_is_never_repaired(acquired,bad):
    ledger,c,e,p=plan(acquired);p['investigation']['recommendations'][0]['action_code']=bad
    with pytest.raises(OutputFailure,match='model_schema'):c.assemble(p,e,ledger)


def test_unread_reference_and_unsupported_definite_customer_state_remain_rejected(acquired):
    ledger,c,e,p=plan(acquired);p['source_statement']['fact_ids']=['V1']
    with pytest.raises(OutputFailure):c.assemble(p,e,ledger)
    p['source_statement']['fact_ids']=['F1'];p['source_statement']['value']='authorized'
    with pytest.raises(OutputFailure,match='statement_inconsistent'):c.assemble(p,e,ledger)


def test_report_does_not_fill_missing_model_customer_evidence(acquired):
    ledger,c,_,p=plan(acquired);e=c.extract(dict(facts=[]),ledger)
    with pytest.raises(OutputFailure):c.assemble(p,e,ledger)
    assert not e['facts']


def test_support_rules_and_cardinality_remain_bounded(acquired):
    ledger,c,e,p=plan(acquired);p['investigation']['recommendations']=[dict(action_code='A2',fact_ids=[],reason='Missing evidence')]
    with pytest.raises(OutputFailure):c.assemble(p,e,ledger)
    p['investigation']['recommendations']=[dict(action_code='A1',fact_ids=[],reason='Review only')]
    p['investigation']['hypotheses']*=7
    with pytest.raises(OutputFailure):c.assemble(p,e,ledger)


def test_cny_display_uses_minor_units_without_changing_sources_or_claiming_prose_verified(acquired):
    ledger,c,e,p=plan(acquired);before=deepcopy(ledger.model_context())
    # Values come only from the host-acquired transaction. No reclassification.
    transaction=next(iter(ledger.transactions.values()));transaction['currency']='CNY';transaction['amount_cents']=4016
    context=c.context_for(e,ledger)
    assert context['host_transaction_amount_display'][0]['amount_display']=='40.16 CNY'
    assert context['acquired']['read_line_handles']==before['read_line_handles']
