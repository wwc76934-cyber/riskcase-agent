"""Independent-question runner contracts, failure denominators and model-input isolation."""

import json
from pathlib import Path
import shutil

import pytest

from app.cli.planner_v2 import CODE, audit, dispatch, load, prepare, run_slot
from app.risk.history import sha256_file
from app.risk.intent_router import NaturalQuestion
from app.risk.planner_v2 import ScriptedPlanner
from tests.test_merchant_research import manifest


@pytest.fixture
def stage(tmp_path, manifest):
    repo = Path(__file__).resolve().parents[1]
    root = tmp_path / "root"
    for name in CODE:
        dest = root / name; dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(repo / name, dest)
    source = root / "source-tasks.json"
    source.write_text(json.dumps([{"manifest": str(manifest), "manifest_sha256": sha256_file(manifest),
                                  "projection_sha256": sha256_file(manifest.parent / "facts.sqlite")}]), encoding="utf-8")
    questions = root / "questions.json"
    questions.write_text(json.dumps({"tasks": [
        {"id": "own-execute", "family": "basic", "question": {"text": "核查频次和新商户"}, "gold": {"decision": "execute", "checks": ["frequency", "merchant"]}},
        {"id": "own-clarify", "family": "broad", "question": {"text": "请分析一下风险"}, "gold": {"decision": "clarify", "checks": []}},
        {"id": "own-reject", "family": "action", "question": {"text": "查频次且执行支付"}, "gold": {"decision": "reject", "checks": []}},
    ]}), encoding="utf-8")
    directory = root / "runs/own-test"
    prepare(root, directory, questions, methods=("keyword", "model", "v1_keyword"), source_tasks=source, cohort_report=manifest.parent.parent / "build-report.json")
    return root, directory


def test_frozen_independent_schema_and_code_mutation(stage):
    root, directory = stage
    contract, tasks = load(root, directory)
    assert contract["tasks"] == 3 and all(t["split"] == "acceptance" for t in tasks)
    path = root / "app/risk/planner_v2.py"; path.write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="Frozen code changed"): load(root, directory)
    assert len(load(root, directory, code_root=directory / "frozen-code")[1]) == 3


def test_keywords_audit_and_missing_slots_do_not_disappear(stage):
    root, directory = stage
    _, tasks = load(root, directory)
    for task in tasks: run_slot(root, directory, task, "keyword")
    result = audit(root, directory)
    counts = result["groups"]["keyword/acceptance"]
    assert counts["tasks"] == counts["exact_scope_matches"] == counts["first_scope_matches"] == 3
    assert counts["conditional_sql_verified_reports"] == 1 and counts["tool_attempts"] == 3
    assert result["groups"]["model/acceptance"]["missing_or_interrupted_slots"] == 3


def test_started_slots_and_failed_slots_are_never_automatically_rerun(stage):
    root, directory = stage
    _, tasks = load(root, directory)
    task = tasks[0]
    output = directory / "model" / task["id"]; output.mkdir(parents=True)
    assert run_slot(root, directory, task, "model", live=True)["status"] == "existing_slot_not_retried"
    result = run_slot(root, directory, tasks[1], "model", live=False)
    assert result["status"] == "slot_failed"
    assert run_slot(root, directory, tasks[1], "model", live=True)["status"] == "existing_slot_not_retried"
    counts = audit(root, directory)["groups"]["model/acceptance"]
    assert counts["tasks"] == 3 and counts["slot_failures"] == 1 and counts["missing_or_interrupted_slots"] == 2


def test_model_result_replay_and_only_question_goes_to_model(stage, monkeypatch):
    root, directory = stage
    _, tasks = load(root, directory)
    class RecordedScript:
        def __init__(self): self.requests = []; self.script = None
        def chat(self, messages, tools=None):
            self.requests.append({"messages": json.loads(json.dumps(messages)), "tools": tools})
            question = NaturalQuestion(json.loads(messages[-1]["content"])["untrusted_question"])
            return ScriptedPlanner(question).chat(messages, tools)
        def close(self): pass
        def scrub(self, value): return value
        def snapshot(self):
            return {"requests_sent": 0, "ledger": {"unknown_cost_requests": 0, "run_committed_estimate_cny": "0"}}
    client = RecordedScript()
    monkeypatch.setattr("app.cli.planner_v2.live_client", lambda *_: client)
    for task in tasks: run_slot(root, directory, task, "model", live=True)
    assert all(r["tools"] == [] for r in client.requests)
    for request in client.requests:
        assert set(json.loads(request["messages"][-1]["content"])) == {"untrusted_question"}
    result = audit(root, directory)
    assert result["groups"]["model/acceptance"]["exact_scope_matches"] == 3
    assert result["groups"]["model/acceptance"]["real_requests"] == 0


def test_tampered_route_refused(stage):
    root, directory = stage
    _, tasks = load(root, directory)
    run_slot(root, directory, tasks[0], "keyword")
    path = directory / "keyword" / tasks[0]["id"] / "route.json"
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="artifact changed"): audit(root, directory)
