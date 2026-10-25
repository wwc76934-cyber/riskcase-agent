from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
import json
import sqlite3

import pytest

from app.risk.data_contract import Transaction
from app.risk.history import Coverage, UserHistoryTool, check_count_threshold, create_history_snapshot
from app.agent.react_agent import Agent
from app.llm.fake_client import FakeLLMClient, make_tool_call_message
from app.tools.history_tools import HistoryTools


START = datetime(2019, 12, 1)
END = datetime(2020, 1, 1)


def transaction(number, event_time, amount="0.10", user="0"):
    return Transaction(
        f"source#{number}", "source", number, user, f"{user}:0", "merchant", event_time, Decimal(amount)
    )


def build(tmp_path, rows, *, scanned=True, verified=True):
    coverage = Coverage(("0", "1"), START, END, "source", scanned, verified)
    manifest = create_history_snapshot(tmp_path / "snapshot", rows, coverage)
    return UserHistoryTool(manifest), manifest


def test_window_includes_start_excludes_focal_and_future(tmp_path):
    cutoff = datetime(2019, 12, 3, 12)
    start = cutoff - timedelta(hours=24)
    rows = [
        transaction(1, start - timedelta(seconds=1)), transaction(2, start),
        transaction(3, cutoff - timedelta(seconds=1)), transaction(4, cutoff),
        transaction(5, cutoff + timedelta(seconds=1)), transaction(6, start, user="1"),
    ]
    tool, _ = build(tmp_path, rows)
    result = tool.query(user_id="0", as_of=cutoff)
    assert result["observed_count"] == 2
    assert result["observed_amount_sum"] == "0.20"
    assert {row["transaction_id"] for row in result["evidence"]} == {"source#2", "source#3"}
    assert result["window_complete_within_source_dataset"] is True


def test_incomplete_low_count_is_unknown_not_safe(tmp_path):
    tool, _ = build(tmp_path, [], scanned=False)
    result = tool.query(user_id="0", as_of=datetime(2019, 12, 3))
    assert check_count_threshold(result, 5) == "unknown_incomplete_window"
    assert "source_scan_incomplete" in result["incomplete_reasons"]


def test_unknown_user_and_out_of_scope_window_do_not_claim_zero_history(tmp_path):
    tool, _ = build(tmp_path, [])
    unknown_user = tool.query(user_id="missing", as_of=datetime(2019, 12, 3))
    early = tool.query(user_id="0", as_of=START)
    assert check_count_threshold(unknown_user, 1) == "unknown_incomplete_window"
    assert "window_outside_snapshot_scope" in early["incomplete_reasons"]


def test_evidence_limit_does_not_truncate_aggregates(tmp_path):
    rows = [transaction(i, datetime(2019, 12, 2, i)) for i in range(1, 8)]
    tool, _ = build(tmp_path, rows)
    result = tool.query(user_id="0", as_of=datetime(2019, 12, 3), evidence_limit=2)
    assert result["observed_count"] == 7
    assert result["observed_amount_sum"] == "0.70"
    assert len(result["evidence"]) == 2 and result["evidence_truncated"]
    assert result["window_complete_within_source_dataset"]
    assert check_count_threshold(result, 5) == "met_in_observed_records"


def test_duplicate_reference_removed_but_conflicting_reference_fails(tmp_path):
    row = transaction(1, datetime(2019, 12, 2))
    _, manifest = build(tmp_path, [row, row])
    assert json.loads(manifest.read_text())["duplicate_references_removed"] == 1
    other = tmp_path / "conflict"
    with pytest.raises(ValueError, match="conflicting"):
        create_history_snapshot(other, [row, replace(row, amount=Decimal("3"))],
                                Coverage(("0",), START, END, "source", True, True))
    assert not (other / "manifest.json").exists()


def test_out_of_scope_input_cannot_create_ready_manifest(tmp_path):
    with pytest.raises(ValueError, match="time scope"):
        build(tmp_path, [transaction(1, END)])
    assert not (tmp_path / "snapshot" / "manifest.json").exists()


def test_hash_change_is_detected_and_database_has_no_labels(tmp_path):
    _, manifest = build(tmp_path, [])
    database = manifest.parent / "facts.sqlite"
    with sqlite3.connect(database) as connection:
        columns = [row[1] for row in connection.execute("PRAGMA table_info(transactions)")]
        assert "is_fraud" not in columns
        connection.execute("CREATE TABLE tampered (value TEXT)")
    with pytest.raises(ValueError, match="hash mismatch"):
        UserHistoryTool(manifest)


def test_query_values_cannot_change_sql(tmp_path):
    tool, _ = build(tmp_path, [transaction(1, datetime(2019, 12, 2))])
    result = tool.query(user_id="0' OR 1=1 --", as_of=datetime(2019, 12, 3))
    assert result["observed_count"] == 0
    assert not result["window_complete_within_source_dataset"]


@pytest.mark.parametrize("kwargs", [{"lookback_hours": 0}, {"lookback_hours": True}, {"evidence_limit": 0}])
def test_invalid_tool_budgets_fail(tmp_path, kwargs):
    tool, _ = build(tmp_path, [])
    with pytest.raises(ValueError):
        tool.query(user_id="0", as_of=datetime(2019, 12, 3), **kwargs)


def test_unverified_source_and_complete_empty_window_are_distinguished(tmp_path):
    tool, _ = build(tmp_path, [], verified=False)
    result = tool.query(user_id="0", as_of=datetime(2019, 12, 3))
    assert "source_integrity_unverified" in result["incomplete_reasons"]
    complete_dir = tmp_path / "complete"
    complete_dir.mkdir()
    complete, _ = build(complete_dir, [])
    result = complete.query(user_id="0", as_of=datetime(2019, 12, 3))
    assert check_count_threshold(result, 5) == "not_met_in_complete_dataset_window"


@pytest.mark.parametrize("name,arguments", [
    ("read_file", '{"path":"evaluation-labels.jsonl"}'),
    ("query_user_history", '{"user_id":"0","as_of":"2019-12-03","sql":"SELECT *"}'),
    ("query_user_history", '[]'),
    ("query_user_history", '{"user_id":"0","as_of":"2019-12-03","lookback_hours":true}'),
])
def test_history_adapter_rejects_extra_capabilities(tmp_path, name, arguments):
    tool, _ = build(tmp_path, [])
    result = HistoryTools(tool).dispatch(name, arguments)
    assert result["ok"] is False and result["error_type"] == "ToolInputError"


def test_agent_calls_real_history_and_receives_label_free_observation(tmp_path):
    tool, _ = build(tmp_path, [transaction(1, datetime(2019, 12, 2, 12))])
    fake = FakeLLMClient([
        make_tool_call_message("history-1", "query_user_history", {"user_id": "0", "as_of": "2019-12-03T00:00:00"}),
        {"role": "assistant", "content": "预设回复结束；不代表真实模型调查能力。"},
    ])
    result = Agent(fake, HistoryTools(tool), system_prompt="Only query history; check coverage.").run("查询历史")
    observation = fake.requests[1]["messages"][-1]
    data = json.loads(observation["content"])
    assert result.status == "completed"
    assert observation["tool_call_id"] == "history-1"
    assert data["ok"] is True and data["data"]["observed_count"] == 1
    assert "is_fraud" not in observation["content"]
    assert [s["function"]["name"] for s in fake.requests[0]["tools"]] == ["query_user_history"]
