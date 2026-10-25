"""Authored gold must score omissions and wrong claims independently of run status."""

from copy import deepcopy
import json
from pathlib import Path

import pytest

from app.llm.offline_investigator import OfflineInvestigationClient
from app.risk.investigation import run_investigation
from app.risk.task_evaluation import build_development_pack, score_task
from app.tools.case_tools import CaseTools


SPEC = Path(__file__).resolve().parents[1] / "config/investigation-development-v1.json"


@pytest.fixture
def tasks(tmp_path):
    return build_development_pack(SPEC,tmp_path / "pack")


def test_pack_provenance_and_scope_trap(tasks):
    assert len(tasks) == 9
    trap = next(task for task in tasks if task["id"] == "future_and_other_user_trap")
    tools = CaseTools(Path(trap["manifest"]))
    assert tools.manifest["prior_rows"] == 1
    assert tools.target["amount_cents"] == 1000
    assert "gold" not in tools.manifest
    assert all(row["user_id"] == "fixture-user" for row in tools.prior)
    assert all("secret" not in row["merchant_id"] for row in tools.prior)


@pytest.mark.parametrize("task_index",range(9))
def test_scripted_control_against_authored_gold(tasks,task_index):
    task = tasks[task_index]
    result = run_investigation(OfflineInvestigationClient(),CaseTools(Path(task["manifest"])),
        client_mode="SCRIPTED_TEST",max_iterations=6)
    score = score_task(result,gold=task["gold"],required_checks=task["required_checks"])
    assert score["task_passed"], (task["id"],result,score)
    # Evaluator answers and scenario names never enter investigator messages.
    text = json.dumps(result["messages"])
    assert '"gold"' not in text and task["id"] not in text


@pytest.mark.parametrize("mutation",["omitted","wrong","duplicate","unknown","failed_status"])
def test_success_flag_alone_cannot_pass_task(mutation):
    result = {"status":"completed_validated","findings":{"findings":[
        {"check":"a","assessment":"not_supported"},{"check":"b","assessment":"insufficient_evidence"}]}}
    if mutation == "omitted": result["findings"]["findings"].pop()
    if mutation == "wrong": result["findings"]["findings"][1]["assessment"] = "supported"
    if mutation == "duplicate": result["findings"]["findings"].append(deepcopy(result["findings"]["findings"][0]))
    if mutation == "unknown": result["findings"]["findings"].append({"check":"unknown","assessment":"supported"})
    if mutation == "failed_status": result["status"] = "report_rejected"
    score = score_task(result,gold={"a":"not_supported","b":"insufficient_evidence"},required_checks=["a","b"])
    assert score["task_passed"] is False


def test_evidence_gap_is_correct_answer_not_failure():
    score = score_task({"status":"completed_validated","findings":{"findings":[
        {"check":"a","assessment":"insufficient_evidence"}]}},
        gold={"a":"insufficient_evidence"},required_checks=["a"])
    assert score["task_passed"] and score["correct_required_checks"] == 1
