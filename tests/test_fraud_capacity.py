from app.cli.fraud_capacity import audit_evidence
from app.investigation.contracts import digest as evidence_digest
from app.service.store import digest as store_digest


def test_auditor_uses_evidence_serialization_contract_not_job_digest():
    result={'source':'fraud_facts','data':{'count':3,'currency':'unspecified'}}
    assert evidence_digest(result)!=store_digest(result)
    assert audit_evidence({'E1':dict(result=result,sha256=evidence_digest(result))})
    assert not audit_evidence({'E1':dict(result=result,sha256=store_digest(result))})
    assert not audit_evidence({'E1':dict(result={'source':'changed'},sha256=evidence_digest(result))})
