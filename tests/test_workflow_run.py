"""Durable stage progress must distinguish success, failure and cancellation."""

import json
import pytest

from app.risk.workflow_run import WorkflowRun


def test_success_is_persisted_before_completion(tmp_path):
    run = WorkflowRun(tmp_path / "run")
    assert run.stage("one",lambda:{"ok":True})=={"ok":True}
    assert json.loads((run.directory / "workflow.json").read_text())["status"]=="running"
    run.complete()
    assert json.loads((run.directory / "workflow.json").read_text())["status"]=="completed"


@pytest.mark.parametrize("error,status",[(ValueError("broken"),"failed"),(KeyboardInterrupt(),"cancelled")])
def test_interrupted_stage_cannot_be_marked_completed(tmp_path,error,status):
    run = WorkflowRun(tmp_path / "run")
    run.stage("one",lambda:{"ok":True})
    def fail(): raise error
    with pytest.raises(type(error)): run.stage("two",fail)
    state = json.loads((run.directory / "workflow.json").read_text())
    assert state["status"]==status and state["stages"][0]["status"]=="completed"
    with pytest.raises(ValueError): run.complete()


def test_existing_run_is_not_overwritten(tmp_path):
    WorkflowRun(tmp_path / "run")
    with pytest.raises(FileExistsError): WorkflowRun(tmp_path / "run")
