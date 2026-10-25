"""Frozen benchmark anti-tamper and wrong-scope simulation semantics."""

import json
from pathlib import Path

import pytest

from app.cli.natural_benchmark import CODE, load, prepare, slot
from app.risk.history import sha256_file
from tests.test_merchant_research import manifest


@pytest.fixture
def stage(tmp_path, manifest):
    repo = Path(__file__).resolve().parents[1]
    root = tmp_path / "repo"
    import shutil
    for name in CODE:
        target = root / name; target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(repo / name, target)
    source = root / "data/cohorts/cohort-3bfdbaab486f/build-report.json"
    source.parent.mkdir(parents=True); shutil.copyfile(manifest.parent.parent / "build-report.json", source)
    (source.parent / "dataset").mkdir()
    shutil.copyfile(manifest.parent.parent / "dataset/facts.sqlite", source.parent / "dataset/facts.sqlite")
    tasks = []
    for i in range(8):
        case = root / f"cases/{i}/manifest.json"; case.parent.mkdir(parents=True)
        for name in ("manifest.json", "facts.sqlite"): shutil.copyfile(manifest.parent / name, case.parent / name)
        tasks.append({"split": "development" if i < 4 else "evaluation", "user_id": str(i), "manifest": str(case),
                      "manifest_sha256": sha256_file(case), "projection_sha256": sha256_file(case.parent / "facts.sqlite")})
    old = root / "runs/mixed-stage-v1/tasks-host-only.json"; old.parent.mkdir(parents=True)
    old.write_text(json.dumps(tasks), encoding="utf-8")
    directory = root / "runs/natural-stage-v1"
    prepare(root, directory)
    return root, directory


def test_frozen_contract_and_mutation_rejection(stage):
    root, directory = stage
    contract, tasks = load(root, directory)
    assert len(tasks) == 80 and contract["max_router_requests"] == 2
    path = root / "app/risk/intent_router.py"; path.write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="Frozen code changed"): load(root, directory)
    assert len(load(root, directory, code_root=directory / "frozen-code")[1]) == 80


def test_started_slot_is_never_retried(stage):
    root, directory = stage
    _, tasks = load(root, directory)
    output = directory / "model" / tasks[0]["id"]; output.mkdir(parents=True)
    assert slot(root, directory, tasks[0], "model", True)["status"] == "existing_slot_not_retried"
    assert not (output / "receipt.json").exists()


def test_conditional_execution_never_uses_gold_to_fix_scope(stage):
    root, directory = stage
    _, tasks = load(root, directory)
    task = tasks[0] | {"gold": {"decision": "reject", "checks": []}}
    assert slot(root, directory, task, "keyword", False)["status"] == "routed"
    output = directory / "keyword" / task["id"]
    receipt = json.loads((output / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["scope_confirmation"] == "SIMULATED_CONFIRMATION_NOT_HUMAN_REVIEW"
    assert receipt["route"]["decision"] == "execute" and receipt["tool_attempts"] == 2


def test_nonexecute_has_no_evidence(stage):
    root, directory = stage
    _, tasks = load(root, directory)
    task = next(t for t in tasks if t["family"] == "ambiguous")
    slot(root, directory, task, "keyword", False)
    output = directory / "keyword" / task["id"]
    receipt = json.loads((output / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["tool_attempts"] == 0 and not (output / "evidence").exists()


def test_audit_counts_missing_slots_and_checks_source_sql(stage):
    from scripts.verify_natural import audit_stage
    root, directory = stage
    _, tasks = load(root, directory)
    slot(root, directory, tasks[0], "keyword", False)
    result = audit_stage(root, directory)
    assert result["groups"]["keyword/development"]["tasks"] == 40
    assert result["groups"]["keyword/development"]["missing_or_interrupted_slots"] == 39
    assert result["groups"]["keyword/development"]["conditional_sql_verified_reports"] == 1
    assert result["groups"]["model/evaluation"]["missing_or_interrupted_slots"] == 40
    path = directory / "keyword" / tasks[0]["id"] / "route.json"
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="artifact changed"): audit_stage(root, directory)
