from copy import deepcopy
import pytest
from app.evaluation.v3_tasks import case
from app.evaluation.v3_narratives import DEVELOPMENT
from app.evaluation.v3_score import score_stage
from app.investigation.v3.world import InvestigationWorld
from app.investigation.v3.output import AcquiredEvidence
from app.investigation.v3.typed_review import TypedReviewContract
from app.investigation.contracts import digest


def fixture(stage,*,read_current=True):
    runtime,reference=case('development','quoted_speech',0,DEVELOPMENT['quoted_speech'][0],1)
    world=InvestigationWorld(runtime);evidence={'E1':dict(result=world.overview(),sha256=digest(world.overview()))}
    for at in range(stage+1):
        if at:world.advance(at)
        ids=[k for k in world.eligible() if k not in world.acquired and (read_current or at<stage)]
        if ids:
            body=world.read(ids);evidence['E'+str(len(evidence)+1)]=dict(result=body,sha256=digest(body))
    ledger=AcquiredEvidence(case_id=runtime['case_id'],cutoff=runtime['cutoff'],as_of=runtime['stage_times'][stage],
        subject_id=runtime['subject_id'],transaction_ids=[r['transaction_id'] for r in runtime['transactions']],target_id=runtime['alert']['transaction_id']).ingest(evidence)
    return runtime,reference,evidence,ledger


def report_for(reference,stage,ledger,*,value=None,disposition='continue_collection',wrong_customer=False):
    contract=TypedReviewContract(ledger.target_id);truth=reference['stages'][stage];expected={f['source_id']:f for f in truth['expected_observations']}
    facts=[]
    for handle,line in ledger.lines.items():
        if line['source_id'] in expected:
            f=expected[line['source_id']];facts.append(dict(predicate=f['predicate'],value='denied' if wrong_customer and f['predicate']=='customer_authorization' else f['value'],refs=[handle]))
    extraction=contract.extract(dict(facts=facts),ledger)
    state=value or truth['expected_source_statement']['value'];support=[k for k,f in extraction['facts'].items() if f['predicate']=='customer_authorization' and f['value']==state]
    payload=dict(investigation=dict(hypotheses=[dict(description='Author-scored fixture',status='open',support=list(extraction['facts']),counterevidence=[],unknowns=['Fraud not confirmed'])],
        tensions=[],timeline_refs=[],recommendations=[dict(action='analyst_review',fact_ids=[],reason='Human review')],missing=truth['required_missing'],stop_reason='sufficient_for_human_review',summary='Unit fixture, not model evidence'),
        source_statement=dict(value=state,fact_ids=support,rationale='Based on acquired sources'),proposed_disposition=disposition)
    return contract.assemble(payload,extraction,ledger)


def test_full_current_evidence_and_correct_target_facts_can_pass_finite_author_contract():
    runtime,reference,evidence,ledger=fixture(2);report=report_for(reference,2,ledger)
    score=score_stage(runtime,reference,2,evidence,report)
    assert score['strict_author_reference_pass'] and score['fact_accuracy_given_report']==1
    assert not score['free_prose_semantics_scored'] and not score['human_review_quality_measured']


def test_unacquired_correction_is_an_acquisition_failure_separate_from_reasoning():
    runtime,reference,evidence,ledger=fixture(2,read_current=False)
    report=report_for(reference,2,ledger,value='unresolved')
    score=score_stage(runtime,reference,2,evidence,report)
    assert score['delivered'] and score['reasoning_correct_given_acquired_sources']
    assert not score['full_information_target_correct'] and score['missing_critical_sources'] and not score['strict_author_reference_pass']


def test_protocol_valid_semantically_wrong_fact_remains_a_business_failure():
    runtime,reference,evidence,ledger=fixture(1)
    report=report_for(reference,1,ledger,value='denied',wrong_customer=True)
    score=score_stage(runtime,reference,1,evidence,report)
    assert score['delivered'] and score['evidence_support_given_report']==1
    assert score['wrong_fact_ids'] and not score['reasoning_correct_given_acquired_sources']


def test_wrong_close_is_scored_as_a_recommendation_without_becoming_an_executed_action():
    runtime,reference,evidence,ledger=fixture(1)
    report=report_for(reference,1,ledger,disposition='close_alert_as_legitimate')
    score=score_stage(runtime,reference,1,evidence,report)
    assert score['delivered'] and score['wrong_alert_close'] and not score['disposition_correct']
    assert report['disposition_executed'] is False and report['human_review_required']


def test_late_stage_still_reopens_on_new_relevant_material_and_nonexpert_does_not_rate_escalation():
    runtime,reference,evidence,ledger=fixture(2)
    assert reference['stages'][2]['reopen_on_new_material']
    score=score_stage(runtime,reference,2,evidence,report_for(reference,2,ledger,disposition='escalate_for_review'))
    assert score['unreasonable_escalation_expert_rate'] is None


def test_old_reference_cannot_be_silently_regraded():
    runtime,reference,evidence,ledger=fixture(1);reference.pop('reference_schema')
    with pytest.raises(ValueError,match='do not replace old'):score_stage(runtime,reference,1,evidence,None)
