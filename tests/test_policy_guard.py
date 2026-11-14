from copy import deepcopy

from app.investigation.v3.policy_guard import apply_policy_guard, canonical_decision


class Ledger:
    lines = {"h1": {}}


def report(**over):
    base = dict(
        proposed_disposition="source_question_resolved", stop_reason="sufficient_for_human_review",
        source_statement=dict(value="denied", fact_ids=["F1"], transaction_id="t"),
        facts=[dict(id="F1", predicate="customer_retraction", value="denied", citations=[dict(handle="h1")])],
        missing=["confirmed_case_outcome"],
        recommendations=[dict(action="analyst_review", fact_ids=[], reason="r")])
    base.update(over)
    return base


def test_supported_resolved_state_gives_resolved_disposition():
    guarded, audit = apply_policy_guard(report(), Ledger)
    assert guarded["proposed_disposition"] == "source_question_resolved"
    assert guarded["stop_reason"] == "sufficient_for_human_review"
    assert audit["guard_interventions"] == []


def test_unsupported_state_is_downgraded_to_continue_collection():
    r = report(facts=[dict(id="F1", predicate="customer_retraction", value="denied", citations=[dict(handle="nope")])])
    guarded, audit = apply_policy_guard(r, Ledger)
    assert guarded["proposed_disposition"] == "continue_collection"
    assert guarded["stop_reason"] == "unavailable_evidence"
    assert {i["field"] for i in audit["guard_interventions"]} == {"proposed_disposition", "stop_reason"}


def test_wrong_value_citation_is_not_support():
    r = report(source_statement=dict(value="authorized", fact_ids=["F1"], transaction_id="t"))
    assert canonical_decision(r, Ledger)["proposed_disposition"] == "continue_collection"


def test_unverified_material_adds_followups_and_extras_become_advisory():
    r = report(missing=["other_unverified_material"],
               recommendations=[dict(action="analyst_review", fact_ids=[], reason="r"),
                                dict(action="check_device_history", fact_ids=[], reason="extra"),
                                dict(action="freeze_account", fact_ids=[], reason="bad")])
    guarded, audit = apply_policy_guard(r, Ledger)
    codes = [a["action"] for a in guarded["recommendations"]]
    assert codes == ["analyst_review", "request_customer_evidence", "verify_contact_channel", "request_missing_window"]
    assert [a["action"] for a in guarded["analyst_advisory_notes"]] == ["check_device_history"]
    assert {"field": "recommendations", "removed_unsafe": "freeze_account"} in audit["guard_interventions"]
    assert audit["raw_llm_decision"]["actions"] == ["analyst_review", "check_device_history", "freeze_account"]


def test_input_report_is_not_modified():
    r = report()
    before = deepcopy(r)
    apply_policy_guard(r, Ledger)
    assert r == before


def test_no_ledger_never_resolves():
    assert canonical_decision(report(), None)["proposed_disposition"] == "continue_collection"
