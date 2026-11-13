"""Deterministic Policy Guard: the host, not the model, decides disposition, stop reason and mandatory actions.

The model supplies cited facts and a customer-state classification. This module
checks that classification against the evidence ledger and maps it through a
fixed SOP matrix. Nothing here depends on a case id or a reference answer.

SOP matrix (state is the report's ``source_statement.value``):

* state authorized or denied AND cited by a customer-source fact the ledger supports
    -> disposition ``source_question_resolved``, stop ``sufficient_for_human_review``
* anything else (unresolved, or resolved without supporting evidence)
    -> disposition ``continue_collection``, stop ``unavailable_evidence``
* mandatory action ``analyst_review`` always
* ``missing`` contains ``other_unverified_material``
    -> also request_customer_evidence, verify_contact_channel, request_missing_window

Model recommendations outside the mandatory set are kept as non-binding
``analyst_advisory_notes``. Unsafe actions are dropped and logged.
"""
from copy import deepcopy

CUSTOMER_PREDICATES = {"customer_authorization", "customer_retraction"}
RESOLVED_STATES = {"authorized", "denied"}
UNSAFE_ACTIONS = {"safe_close", "confirmed_fraud", "confirm_fraud", "refund", "freeze_account", "write_fraud_label"}
MISSING_TO_ACTIONS = {
    "other_unverified_material": ("request_customer_evidence", "verify_contact_channel", "request_missing_window"),
}
BASE_ACTIONS = ("analyst_review",)


def statement_supported(report, ledger):
    """True when the stated customer state cites a ledger-backed customer fact with the same value."""
    statement = report.get("source_statement") or {}
    state = statement.get("value")
    if state not in RESOLVED_STATES or ledger is None:
        return False
    facts = {f.get("id"): f for f in report.get("facts", [])}
    for fact_id in statement.get("fact_ids", ()):
        fact = facts.get(fact_id)
        if (fact and fact.get("predicate") in CUSTOMER_PREDICATES and fact.get("value") == state
                and fact.get("citations")
                and all(c.get("handle") in ledger.lines for c in fact["citations"])):
            return True
    return False


def canonical_decision(report, ledger):
    state = (report.get("source_statement") or {}).get("value")
    resolved = statement_supported(report, ledger)
    disposition = "source_question_resolved" if resolved else "continue_collection"
    stop = "sufficient_for_human_review" if resolved else "unavailable_evidence"
    actions = list(BASE_ACTIONS)
    for code in report.get("missing", []):
        for action in MISSING_TO_ACTIONS.get(code, ()):
            if action not in actions:
                actions.append(action)
    return dict(proposed_disposition=disposition, stop_reason=stop, mandatory_actions=actions,
                state_used=state if resolved else "unresolved")


def apply_policy_guard(report, ledger):
    """Return ``(guarded_report, audit)``. The input report is not modified."""
    raw = dict(proposed_disposition=report.get("proposed_disposition"), stop_reason=report.get("stop_reason"),
               actions=[r.get("action") for r in report.get("recommendations", [])])
    decision = canonical_decision(report, ledger)
    guarded = deepcopy(report)
    interventions = []
    if raw["proposed_disposition"] != decision["proposed_disposition"]:
        interventions.append(dict(field="proposed_disposition", from_=raw["proposed_disposition"],
                                  to=decision["proposed_disposition"]))
    if raw["stop_reason"] != decision["stop_reason"]:
        interventions.append(dict(field="stop_reason", from_=raw["stop_reason"], to=decision["stop_reason"]))
    guarded["proposed_disposition"] = decision["proposed_disposition"]
    guarded["stop_reason"] = decision["stop_reason"]
    by_action = {}
    for item in report.get("recommendations", []):
        by_action.setdefault(item.get("action"), item)
    mandatory, advisory = [], []
    for action in decision["mandatory_actions"]:
        mandatory.append(deepcopy(by_action.get(action)) or dict(
            action=action, fact_ids=[], reason="Mandated by the host policy matrix"))
        if action not in by_action:
            interventions.append(dict(field="recommendations", added=action))
    for action, item in by_action.items():
        if action in decision["mandatory_actions"]:
            continue
        if action in UNSAFE_ACTIONS:
            interventions.append(dict(field="recommendations", removed_unsafe=action))
        else:
            advisory.append(deepcopy(item))
            interventions.append(dict(field="recommendations", moved_to_advisory=action))
    guarded["recommendations"] = mandatory
    guarded["analyst_advisory_notes"] = advisory
    audit = dict(raw_llm_decision=raw,
                 policy_guard_decision=dict(proposed_disposition=decision["proposed_disposition"],
                                            stop_reason=decision["stop_reason"],
                                            actions=decision["mandatory_actions"]),
                 guard_interventions=interventions, state_used=decision["state_used"])
    return guarded, audit
