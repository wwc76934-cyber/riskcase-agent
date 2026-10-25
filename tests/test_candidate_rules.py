"""Incremental benefit must exclude baseline overlaps and disclose extra reviews."""

from copy import deepcopy
import pytest

from app.risk.candidate_rules import compile_candidate, replay_candidate


def spec(): return {"version":1,"name":"test_rule","all":[{"field":"amount_cents","op":"gt","value":100}]}


def row(identity,amount,fraud):
    return {"transaction_id":identity,"split":"validation","amount_cents":amount,"is_fraud":fraud}


def test_incremental_counts_exclude_overlap_and_preserve_baseline():
    rows = [row("a",200,True),row("b",10,False),row("c",200,True),row("d",200,False),row("e",200,True),row("f",10,True)]
    result = replay_candidate(rows,{"a","b","e"},spec())
    assert result["baseline"] == {"alerts":3,"true_positives":2,"false_positives":1,"precision":2/3,"recall":.5}
    assert result["overlap_alerts"] == 2
    assert result["added"] == {"alerts":2,"true_positives":1,"false_positives":1,"precision":.5,"recall":.25}
    assert result["union"]["alerts"] == 5 and result["union"]["true_positives"] == 3


def test_equal_threshold_and_zero_positive_metrics():
    result = replay_candidate([row("a",100,False)],set(),spec())
    assert result["candidate"]["alerts"] == 0
    assert result["added"]["precision"] is None and result["added"]["recall"] is None


@pytest.mark.parametrize("field,value,op",[("is_fraud",1,"eq"),("split",1,"eq"),("amount_cents",True,"gt"),
    ("amount_cents",1.5,"gt"),("amount_cents",1,"eval"),("DROP TABLE",1,"eq"),
    ("history_30d_complete",2,"eq"),("merchant_seen_30d",0,"gt"),("count_1h",-1,"gt")])
def test_restricted_language_rejects_labels_code_and_invalid_thresholds(field,value,op):
    candidate = spec()
    candidate["all"] = [{"field":field,"value":value,"op":op}]
    with pytest.raises(ValueError): compile_candidate(candidate)


def test_all_fields_validated_even_when_first_condition_is_false():
    candidate = spec()
    candidate["all"].append({"field":"has_history_30d","op":"eq","value":1})
    with pytest.raises(ValueError): compile_candidate(candidate)({"amount_cents":0})


@pytest.mark.parametrize("problem",["test_split","nonbool_label","duplicate_id","baseline_outside"])
def test_replay_rejects_wrong_scope_and_incomplete_join(problem):
    rows,baseline = [row("a",200,True)],set()
    if problem=="test_split": rows[0]["split"] = "test"
    if problem=="nonbool_label": rows[0]["is_fraud"] = 1
    if problem=="duplicate_id": rows.append(deepcopy(rows[0]))
    if problem=="baseline_outside": baseline = {"unknown"}
    with pytest.raises(ValueError): replay_candidate(rows,baseline,spec())
