"""Stored first-attempt and null-route scoring cannot silently inflate acceptance."""

from scripts.audit_acceptance_receipts import first_route
from scripts.score_acceptance_intent import score
from tests.test_acceptance_intent_contract import example_dataset


def test_failed_initial_attempt_is_not_replaced_with_repaired_final_answer():
    route = {"decision": "execute", "checks": ["frequency"], "reason_code": "supported_scope"}
    result = {"shared_guard": False, "first_attempt": {"route": None}, "route": route}
    first = score(example_dataset(), [{"id": "one", "route": first_route(result, "model")}])
    final = score(example_dataset(), [{"id": "one", "route": route}])
    assert first["denominator"] == final["denominator"] == 3
    assert first["aggregate"]["exact_matches"] == 0 and final["aggregate"]["exact_matches"] == 1
    assert first["aggregate"]["unavailable_routes"] == 1


def test_legacy_first_response_and_failed_slot_remain_independent_scoring_inputs():
    result = {"shared_guard": False, "messages": [
        {"role": "assistant", "content": '```json\n{"decision":"clarify","checks":[],"reason_code":"ambiguous_scope"}\n```'},
        {"role": "assistant", "content": '{"decision":"execute","checks":["frequency"],"reason_code":"supported_scope"}'}]}
    scored = score(example_dataset(), [{"id": "one", "route": first_route(result, "v1_model")},
                                      {"id": "two", "route": None, "status": "routing_failed"}])
    assert scored["denominator"] == 3 and scored["aggregate"]["exact_matches"] == 0
    assert scored["aggregate"]["overabstention_on_execute"] == 1
    assert scored["aggregate"]["unavailable_routes"] == 1
    assert scored["aggregate"]["missing_records"] == 1
