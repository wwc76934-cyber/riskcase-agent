import ast,inspect
import pytest
from app.investigation.v3 import rule_workflow
from app.investigation.v3.rule_workflow import customer_state,collect_rule_workflow,rule_report,machine_auth_observations
from app.investigation.v3.hybrid_review import HybridReviewContract
from app.investigation.v3.delivery import StagedDelivery
from app.service.v3_phase_store import DeliveryPhase
from tests.test_v3_source_access import runtime,start
from tests.test_v3_reliability import Client
from tests.test_v3_phase_store import probe_report


@pytest.mark.parametrize('text,expected',[
    ('客服转来的短信说“txn-target不是我买的”，我没有回答是不是我的。','unresolved'),
    ('我的回答：txn-target是我本人同意的。','authorized'),
    ('我本人没有同意txn-target。','denied'),
    ('txn-other不是我安排的，但txn-target是我本人同意的。','authorized'),
    ('我未曾否认txn-target。','unresolved'),
    ('接听者自称持卡人，说txn-target未经我允许。','unresolved'),
    ('TARGET是我同意的，这里没有识别具体支付。','unresolved'),
])
def test_rules_handle_scope_quotation_and_nonaffirmation_without_case_answers(text,expected):
    assert customer_state(text,'txn-target',{'txn-other'})==expected


def test_rule_workflow_uses_only_available_tools_and_actual_source_machine_fields(tmp_path,runtime):
    _,_,source=start(tmp_path,runtime)
    acquired=collect_rule_workflow(source);report=rule_report(acquired)
    assert report['source_statement']['value']=='unresolved' and report['observation_producer'].startswith('deterministic')
    assert machine_auth_observations(acquired)[0]['value']=='success'
    assert source.position()['stage']==0 and len(source.evidence())==2


def test_hybrid_skips_model_extraction_only_when_customer_original_is_absent(tmp_path,runtime):
    _,_,source=start(tmp_path,runtime);acquired=collect_rule_workflow(source);phase=DeliveryPhase(source,0)
    client=Client([probe_report()]);report=StagedDelivery(phase,phase.claim,acquired,client,max_requests=2,report_contract=HybridReviewContract(acquired.target_id)).run()
    assert client.calls==1 and report['observation_producers']=={'F1':'literal_machine_field'}
    assert report['facts'][0]['predicate']=='authentication' and report['facts'][0]['value']=='success'
    source.advance(1);later=collect_rule_workflow(source)
    assert HybridReviewContract(later.target_id).initial_extraction(later) is None


def test_baseline_has_no_reference_builder_or_family_dispatch_imports():
    tree=ast.parse(inspect.getsource(rule_workflow))
    imports=[node.module for node in ast.walk(tree) if isinstance(node,ast.ImportFrom)]
    assert not any(name and name.startswith('app.evaluation') for name in imports)
    attributes={node.attr for node in ast.walk(tree) if isinstance(node,ast.Attribute)}
    assert not {'snapshot','reference','truth'}&attributes
