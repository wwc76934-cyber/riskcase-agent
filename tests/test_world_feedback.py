from copy import deepcopy
import json
import pytest
from tests.test_fraud_inquiry import inquiry_snapshot
from app.evaluation.fraud_world_data import make_case
from app.evaluation.world_feedback import publish_methods
from app.evaluation.world_score import score
from app.investigation.world_service import WorldService
from app.investigation.world_baseline import BaselineProbeClient
from app.investigation.world_memory import WorldMemory,recall
from app.investigation.harness import HarnessWorker
from app.service.store import Conflict


class FaultInjectionClient(BaselineProbeClient):
    """Intentional unit-test fault, never reported as an observed LLM failure."""
    def chat(self,messages,tools=None):
        response=super().chat(messages,tools)
        if response.get('content'):
            answer=json.loads(response['content'])
            for claim in answer['observations']:
                if claim['predicate']=='customer_authorization':claim['value']='authorized'
            response['content']=json.dumps(answer)
        return response


def test_reference_detected_semantic_error_has_source_bound_method_release(tmp_path,inquiry_snapshot):
    runtime,reference=make_case(inquiry_snapshot,'auth_denial',1,'feedback-fixture')
    service=WorldService(tmp_path/'service',strategy='all_materials')
    job,_=service.submit('fault',runtime,'Probe independent feedback')
    row=HarnessWorker(service,client_factory=FaultInjectionClient).step()
    assert row['state']=='awaiting_review',row['reason']
    result=score(runtime,reference,row['result']['answer'],host_accepted=True)
    assert result['unsupported_claim_ids'] and not result['strict_reference_passed']
    published=publish_methods(service.store,job,runtime,reference)
    assert published['feedback']['source_client_kind']=='simulation'
    assert published['published']
    memory=WorldMemory(service.store);frozen=memory.freeze(runtime)
    shown=recall(frozen)
    assert shown['records'] and all(set(r)=={'memory_id','lesson','topic','policy_versions'} for r in shown['records'])
    assert 'source_target_transaction_id' not in json.dumps(shown)
    candidate=published['published'][0]['candidate'];before=memory.head()['head']
    memory.withdraw(candidate,before)
    assert len(memory.freeze(runtime)['records'])<len(frozen['records'])
    assert frozen['records']  # Already frozen tasks retain their snapshot.
    with pytest.raises(Conflict):memory.withdraw(candidate,before)


def test_memory_override_cannot_impersonate_reviewed_release(tmp_path,inquiry_snapshot):
    override=dict(controlled_stress_not_release=True,records=[dict(memory_id='Mtest',lesson='Untrusted advice',topic='authorization',policy_versions=[1])],rejected=[])
    with pytest.raises(ValueError):WorldService(tmp_path/'forged',memory_mode='reviewed',memory_snapshot=override)
    runtime,_=make_case(inquiry_snapshot,'auth_denial',0,'memory-mode')
    a=WorldService(tmp_path/'modes',memory_mode='wrong',memory_snapshot=override)
    a.submit('same-request',runtime,'Investigate')
    b=WorldService(a.directory,store=a.store,memory_mode='none')
    with pytest.raises(Conflict,match='memory_condition'):b.submit('same-request',runtime,'Investigate')


def test_stress_memory_uses_real_expiration_gate_and_bounded_raw_history(inquiry_snapshot):
    from app.evaluation.world_feedback import memory_condition
    runtime,_=make_case(inquiry_snapshot,'auth_denial',0,'stress')
    stale=memory_condition('stale',runtime,'prior',now=1000)
    assert not stale['records'] and stale['rejected'][0]['reason']=='expired'
    wrong=memory_condition('wrong',runtime,'prior',now=1000)
    assert len(wrong['records'])==1 and not wrong['rejected']
    raw=memory_condition('raw_history',runtime,'长材料'*10000,now=1000)
    assert len(raw['records'][0]['lesson'].encode())<=14000


def test_memory_condition_supplies_context_without_claiming_model_recall(tmp_path,inquiry_snapshot):
    from app.evaluation.world_feedback import memory_condition
    runtime,_=make_case(inquiry_snapshot,'auth_denial',1,'supplied-method-probe')
    snapshot=memory_condition('wrong',runtime,'prior',now=1000)
    class Probe(BaselineProbeClient):
        def chat(self,messages,tools=None):
            frame=json.loads(messages[1]['content'])
            assert any(o.get('kind')=='method_memory' and o['records'] for o in frame['observations'])
            assert 'recall_world_methods' not in {t['function']['name'] for t in tools}
            return super().chat(messages,tools)
    service=WorldService(tmp_path,memory_mode='wrong',memory_snapshot=snapshot)
    job,_=service.submit('memory-exposure',runtime,'Verify experimental treatment delivery, not model benefit')
    row=HarnessWorker(service,client_factory=Probe).step()
    assert row['state']=='awaiting_review',row['reason']
    assert row['result']['method_memory_snapshot']['context_supplied'] is True
    assert row['result']['method_memory_snapshot']['recalled'] is False
