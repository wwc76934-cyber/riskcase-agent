"""Independent host-only acceptance contract and scorer boundaries; never call a model."""

from copy import deepcopy
import json
from pathlib import Path

import pytest

from scripts.score_acceptance_intent import load_tasks, normalized_route, score, strict_load


DATASET = Path(__file__).resolve().parents[1] / "config/acceptance-intent-v2-host-only.json"


def example_dataset():
    return {"dataset_id": "fixture", "limitations": ["fixture"], "policy": {"scoring": "exact"}, "tasks": [
        {"id": "one", "family": "execute", "question": {"text": "fixture one"}, "gold": {"decision": "execute", "checks": ["frequency"]}, "gold_reason": "fixture"},
        {"id": "two", "family": "clarify", "question": {"text": "fixture two"}, "gold": {"decision": "clarify", "checks": []}, "gold_reason": "fixture"},
        {"id": "three", "family": "reject", "question": {"text": "fixture three"}, "gold": {"decision": "reject", "checks": []}, "gold_reason": "fixture"}]}


def test_frozen_dataset_composition_and_unique_texts():
    contract, dataset = load_tasks(DATASET)
    assert dataset["counts"] == {"tasks": 100, "execute": 60, "clarify": 20, "reject": 20}
    assert len({t["question"]["text"] for t in dataset["tasks"]}) == 100
    assert all(1 <= len(t["question"]["text"]) <= 800 for t in dataset["tasks"])
    assert contract["implementation_base_commit"] == "3dea958"
    assert contract["planner_v2_seen_before_freeze"] is False


def test_frozen_dataset_hash_change_rejected(tmp_path):
    changed = tmp_path / DATASET.name
    dataset = json.loads(DATASET.read_text(encoding="utf-8"))
    dataset["tasks"][0]["question"]["text"] += " changed"
    changed.write_text(json.dumps(dataset), encoding="utf-8")
    with pytest.raises(ValueError, match="frozen_acceptance_dataset_changed"):
        load_tasks(changed, DATASET.with_name("acceptance-intent-v2-contract.json"))


def test_missing_invalid_and_false_execute_remain_in_denominator():
    dataset = example_dataset()
    result = score(dataset, [
        {"id": "one", "route": {"decision": "execute", "checks": ["frequency"], "reason_code": "supported_scope"}},
        {"id": "two", "route": {"decision": "execute", "checks": ["amount"], "reason_code": "supported_scope"}}])
    assert result["denominator"] == 3 and result["aggregate"]["exact_matches"] == 1
    assert result["aggregate"]["false_execute_on_nonexecute"] == 1
    assert result["aggregate"]["missing_records"] == 1
    assert result["aggregate"]["overscoped_execute_requests"] == 1


def test_unknown_or_duplicate_results_cannot_hide_failures():
    for records, message in (([{"id": "missing", "route": None}], "unknown"),
                             ([{"id": "one", "route": None}, {"id": "one", "route": None}], "duplicate")):
        with pytest.raises(ValueError, match=message): score(example_dataset(), records)


@pytest.mark.parametrize("route", [
    {"decision": "execute", "checks": ["sql"], "reason_code": "supported_scope"},
    {"decision": "execute", "checks": ["frequency", "frequency"], "reason_code": "supported_scope"},
    {"decision": "clarify", "checks": ["frequency"], "reason_code": "ambiguous_scope"},
    {"decision": "reject", "checks": [], "reason_code": "supported_scope"},
    {"decision": "execute", "checks": ["frequency"], "reason_code": "supported_scope", "approval": True},
    {"decision": "execute", "checks": [], "reason_code": "supported_scope"},
])
def test_illegal_route_contracts_fail(route):
    with pytest.raises(ValueError): normalized_route(route)
    result = score(example_dataset(), [{"id": "one", "route": route}])
    assert result["aggregate"]["invalid_routes"] == 1 and result["aggregate"]["exact_matches"] == 0


def test_check_order_is_not_semantic_and_reason_is_not_exact_gold():
    dataset = example_dataset()
    dataset["tasks"][0]["gold"]["checks"] = ["frequency", "merchant"]
    result = score(dataset, [{"id": "one", "route": {"decision": "execute", "checks": ["merchant", "frequency"], "reason_code": "supported_scope"}},
                             {"id": "three", "route": {"decision": "reject", "checks": [], "reason_code": "restricted_request"}}])
    assert result["aggregate"]["exact_matches"] == 2


def test_failure_routes_and_overabstention_are_distinct():
    result = score(example_dataset(), [{"id": "one", "route": {"decision": "clarify", "checks": [], "reason_code": "ambiguous_scope"}},
                                      {"id": "two", "route": None, "status": "transport_failed"}])
    assert result["aggregate"]["overabstention_on_execute"] == 1
    assert result["aggregate"]["unavailable_routes"] == 1
    assert result["aggregate"]["missing_checks"] == 1


@pytest.mark.parametrize("raw", ['{"results":[],"results":[]}', '{"number": NaN}'])
def test_ambiguous_json_rejected(tmp_path, raw):
    path = tmp_path / "results.json"; path.write_text(raw, encoding="utf-8")
    with pytest.raises(ValueError): strict_load(path)
