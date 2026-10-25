from copy import deepcopy
import json
from pathlib import Path
import sqlite3

import pytest

from app.cli.merchant_benchmark import aggregate,run_batch
from app.risk.merchant_evaluation import build_merchant_pack
from app.risk.merchant_research import MerchantQuestion,MemoizedMerchantTools,PreloadedMerchantTools,PreloadedScriptedClient,run_merchant_research
from app.risk.source_merchant_pack import select_users,sql_reference,load_frozen_pack


@pytest.fixture(scope="module")
def task(tmp_path_factory):
    root=Path(__file__).resolve().parents[1]
    return build_merchant_pack(root / "config/merchant-development-v1.json",tmp_path_factory.mktemp("stage") / "pack")[0]


def test_cache_uses_defensive_copies_and_same_evidence(task):
    tools=MemoizedMerchantTools(Path(task["manifest"]),max_calls=3)
    first=tools.dispatch("get_alert_transaction","{}")
    identity=first["data"]["evidence_id"]
    first["data"]["transaction"]["amount_cents"]=0
    repeated=tools.dispatch("get_alert_transaction","{}")
    assert repeated["data"]["evidence_id"]==identity and repeated["data"]["transaction"]["amount_cents"]==9999
    assert tools.trace[-1]["cache_hit"] is True and tools.attempts==2
    tools.dispatch("get_alert_transaction","{}")
    assert tools.dispatch("get_alert_transaction","{}")["error_type"]=="ToolBudgetExceeded"


def test_invalid_queries_never_reuse_success_cache(task):
    tools=MemoizedMerchantTools(Path(task["manifest"]))
    tools.dispatch("summarize_merchant_distribution",'{"lookback_hours":168}')
    assert not tools.dispatch("summarize_merchant_distribution",'{"lookback_hours":true}')["ok"]
    assert not tools.dispatch("summarize_merchant_distribution",'{"lookback_hours":168,"lookback_hours":168}')["ok"]
    assert not tools.dispatch("get_alert_transaction",'{"user_id":"other"}')["ok"]


def test_cache_is_not_shared_between_instances(task):
    for _ in range(2):
        tools=MemoizedMerchantTools(Path(task["manifest"]))
        tools.dispatch("get_alert_transaction","{}")
        assert tools.trace[-1]["cache_hit"] is False


def test_preloaded_arm_has_same_facts_but_no_executable_model_tools(task):
    question=MerchantQuestion(**task["question"])
    tools=PreloadedMerchantTools(Path(task["manifest"]),question)
    assert tools.attempts==3 and tools.schemas==[]
    result=run_merchant_research(PreloadedScriptedClient(question,tools.evidence),tools,question,client_mode="OFFLINE",evidence_preloaded=True)
    assert result["status"]=="completed_validated" and result["client_requests"]==1
    assert "HOST_PRELOADED_EVIDENCE_DATA" in result["messages"][1]["content"]
    assert not tools.dispatch("get_alert_transaction","{}")["ok"]


def test_preloaded_mode_rejects_unlocked_tools(task):
    with pytest.raises(ValueError):
        run_merchant_research(None,MemoizedMerchantTools(Path(task["manifest"])),MerchantQuestion(),client_mode="TEST",evidence_preloaded=True)


def test_user_selection_is_order_invariant_and_uses_earliest_queue_record():
    rows=[{"user_id":str(i),"transaction_id":f"{i}-later","event_time":"2018-02-01"} for i in range(8)]
    rows += [{"user_id":str(i),"transaction_id":f"{i}-earlier","event_time":"2018-01-01"} for i in range(8)]
    a=select_users(rows,count=4)
    assert a==select_users(list(reversed(rows)),count=4)
    assert len({r["user_id"] for r in a})==4 and all(r["transaction_id"].endswith("earlier") for r in a)
    with pytest.raises(ValueError): select_users(rows,count=20)


def test_sql_reference_independent_of_tool_arithmetic(task):
    manifest=json.loads(Path(task["manifest"]).read_text())
    with sqlite3.connect(Path(task["cohort_report"]).parent / "dataset/facts.sqlite") as db:
        assert sql_reference(db,manifest,MerchantQuestion(**task["question"]))==task["gold"]


def test_failures_and_unknown_requests_stay_in_aggregate():
    rows=[{"arm":"agent","status":"completed_validated","score":{"task_passed":True,"first_attempt_passed":False},"source_sql_verified":True,"real_LLM_requests":3},
        {"arm":"agent","status":"running","score":{"task_passed":False},"real_LLM_requests":None}]
    result=aggregate(rows)["agent"]
    assert result["tasks"]==2 and result["passed"]==1 and result["first_attempt_passed"]==0
    assert result["known_requests"]==3 and result["unknown_request_slots"]==1


def test_verified_status_is_required_for_pass():
    result=aggregate([{"arm":"agent","status":"failed_or_interrupted","score":{"task_passed":True},"real_LLM_requests":0}])
    assert result["agent"]["passed"]==0


def test_frozen_pack_detects_code_changes(tmp_path):
    from app.risk.history import sha256_file
    from app.risk.source_merchant_pack import write_json
    (tmp_path / "config").mkdir()
    (tmp_path / "config/siliconflow-live-v1.json").write_text("{}")
    source=tmp_path / "example.py";source.write_text("original")
    pack=tmp_path / "pack";pack.mkdir()
    write_json(pack / "tasks-host-only.json",[])
    write_json(pack / "contract.json",{"tasks_sha256":sha256_file(pack / "tasks-host-only.json"),"policy_sha256":sha256_file(tmp_path / "config/siliconflow-live-v1.json"),"code_file_sha256":{"example.py":sha256_file(source)}})
    load_frozen_pack(tmp_path,pack)
    source.write_text("changed")
    with pytest.raises(ValueError): load_frozen_pack(tmp_path,pack)


def test_interrupted_receipt_is_not_repeated(tmp_path,monkeypatch):
    import app.cli.merchant_benchmark as module
    pack=tmp_path / "pack";pack.mkdir();(pack / "contract.json").write_text("{}")
    contract={"arms":["agent"],"limits":[]}
    task={"id":"u00-168-720","split":"development"}
    monkeypatch.setattr(module,"load_frozen_pack",lambda *_:(contract,[task]))
    def forbidden(*args,**kwargs): raise AssertionError("Paid slot was repeated")
    monkeypatch.setattr(module.subprocess,"run",forbidden)
    from app.risk.source_merchant_pack import write_json
    from app.risk.history import sha256_file
    output=tmp_path / "output";output.mkdir()
    write_json(output / "binding.json",{"contract_sha256":sha256_file(pack / "contract.json"),"split":"development","live":True})
    write_json(output / "u00-168-720-agent.json",{"task_id":task["id"],"arm":"agent","status":"running","score":{"task_passed":False},"real_LLM_requests":None})
    report=run_batch(tmp_path,pack,output,split="development",live=True)
    assert report["aggregate"]["agent"]["unknown_request_slots"]==1 and report["recorded_slots"]==1
