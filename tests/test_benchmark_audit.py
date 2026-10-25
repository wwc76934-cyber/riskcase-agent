import json

import pytest

import scripts.verify_merchant_benchmark as audit
from app.cli.merchant_benchmark import aggregate
from app.risk.history import sha256_file
from app.risk.source_merchant_pack import write_json


def setup_batch(tmp_path,monkeypatch):
    pack=tmp_path / "pack";pack.mkdir()
    (pack / "contract.json").write_text("{}")
    output=tmp_path / "output";output.mkdir()
    task={"id":"u00-168-720","split":"development"}
    monkeypatch.setattr(audit,"load_frozen_pack",lambda *_:({"arms":["agent"]},[task]))
    binding={"contract_sha256":sha256_file(pack / "contract.json"),"split":"development","live":True}
    write_json(output / "binding.json",binding)
    return pack,output,binding


def test_missing_task_slots_rejected(tmp_path,monkeypatch):
    pack,output,binding=setup_batch(tmp_path,monkeypatch)
    write_json(output / "report.json",{**binding,"records":[],"aggregate":aggregate([]),"expected_slots":1,"recorded_slots":0})
    with pytest.raises(ValueError,match="Missing"): audit.verify_batch(tmp_path,pack,output)


def test_running_slot_is_not_a_finished_stage(tmp_path,monkeypatch):
    pack,output,binding=setup_batch(tmp_path,monkeypatch)
    row={"task_id":"u00-168-720","arm":"agent","status":"running","score":{"task_passed":False},"real_LLM_requests":None}
    write_json(output / "u00-168-720-agent.json",row)
    write_json(output / "report.json",{**binding,"records":[row],"aggregate":aggregate([row]),"expected_slots":1,"recorded_slots":1})
    with pytest.raises(ValueError,match="Interrupted"): audit.verify_batch(tmp_path,pack,output)


def test_failure_is_retained_and_aggregate_cannot_invent_a_pass(tmp_path,monkeypatch):
    pack,output,binding=setup_batch(tmp_path,monkeypatch)
    row={"task_id":"u00-168-720","arm":"agent","status":"failed_or_interrupted","score":{"task_passed":False},"real_LLM_requests":None}
    write_json(output / "u00-168-720-agent.json",row)
    report={**binding,"records":[row],"aggregate":aggregate([row]),"expected_slots":1,"recorded_slots":1}
    write_json(output / "report.json",report)
    assert audit.verify_batch(tmp_path,pack,output)["failed_slots_retained"]==1
    report["aggregate"]["agent"]["passed"]=1
    write_json(output / "report.json",report)
    with pytest.raises(ValueError,match="Aggregate"): audit.verify_batch(tmp_path,pack,output)
