import sqlite3

import pytest

from app.investigation.v3.guard_audit import read, record
from app.investigation.v3.policy_guard import apply_policy_guard, derive_predicates
from tests.test_policy_guard import Ledger, report


def test_predicates_reflect_ledger_support_and_conflict():
    p = derive_predicates(report(), Ledger)
    assert p["customer_statement_state"] == "denied" and p["claim_supported_by_ledger"]
    assert p["evidence_sufficiency"] == "SUFFICIENT_FOR_FINAL_DECISION" and not p["key_conflict_detected"]
    r = report(facts=[dict(id="F1", predicate="customer_retraction", value="denied", citations=[dict(handle="h1")]),
                      dict(id="F2", predicate="customer_authorization", value="authorized", citations=[dict(handle="h1")])])
    assert derive_predicates(r, Ledger)["key_conflict_detected"]


def test_unsupported_claim_is_unresolved_and_exhaustion_is_reported():
    r = report(facts=[])
    assert derive_predicates(r, Ledger)["evidence_sufficiency"] == "AWAITING_INCREMENTAL_EVIDENCE"
    p = derive_predicates(r, Ledger, exhausted=True)
    assert p["customer_statement_state"] == "unresolved" and p["claimed_state"] == "denied"
    assert p["evidence_sufficiency"] == "BUDGET_OR_CHANNEL_EXHAUSTED"


def test_three_state_audit_round_trips_and_is_append_only(tmp_path):
    _, audit = apply_policy_guard(report(missing=["other_unverified_material"]), Ledger)
    path = tmp_path / "audit.sqlite"
    record(path, "run1", "case1", "B4N", 1, audit)
    row, = read(path, "run1")
    assert row["raw_llm_decision"]["actions"] == ["analyst_review"]
    assert row["policy_guard_decision"]["actions"][-1] == "request_missing_window"
    assert row["structured_predicates"]["missing_required_sources"] == ["other_unverified_material"]
    with pytest.raises(sqlite3.IntegrityError):
        record(path, "run1", "case1", "B4N", 1, audit)
