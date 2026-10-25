"""Case permissions, exact financial facts, citation checks and control paths."""

from copy import deepcopy
import json
import sqlite3

import httpx
import pytest

from app.llm.fake_client import FakeLLMClient, make_tool_call_message
from app.llm.glm_client import GLMClient
from app.llm.offline_investigator import OfflineInvestigationClient
from app.risk.case_snapshot import export_case
from app.risk.history import sha256_file
from app.risk.investigation import run_investigation, validate_findings
from app.tools.case_tools import CaseTools


def build(tmp_path, *, coverage_start="2017-12-01T00:00:00", frequent=False, merchant="new", scanned=True, amount=10000):
    source = tmp_path / "source.sqlite"
    with sqlite3.connect(source) as db:
        db.execute("""CREATE TABLE transactions(transaction_id TEXT PRIMARY KEY,source_row INTEGER,
            user_id TEXT,card_id TEXT,event_time TEXT,amount_cents INTEGER,merchant_id TEXT,channel TEXT,mcc TEXT,split TEXT)""")
        rows = [
            ("current",1,"u0","c0","2018-02-01T12:00:00",amount,merchant,"Chip","1234","validation"),
            ("edge",2,"u0","c0","2018-02-01T11:00:00",1000,"old","Chip","1234","validation"),
            ("before",3,"u0","c0","2018-02-01T10:59:59",2000,"old","Chip","1234","validation"),
            ("negative",4,"u0","c0","2018-02-01T11:30:00",-500,"old","Chip","1234","validation"),
            ("peer",5,"u0","c0","2018-02-01T12:00:00",900000,"secret-peer","Chip","1234","validation"),
            ("future",6,"u0","c0","2018-02-01T12:01:00",900000,"secret-future","Chip","1234","validation"),
            ("other-user",7,"u1","c0","2018-02-01T11:10:00",900000,"secret-other-user","Chip","1234","validation"),
            ("too-old",8,"u0","c0","2017-12-01T00:00:00",900000,"secret-old","Chip","1234","train"),
            ("final-test",9,"u0","c0","2019-02-01T12:00:00",900000,"secret-test","Chip","1234","test"),
        ]
        if frequent:
            rows += [(f"burst-{i}",10+i,"u0","c0",f"2018-02-01T11:{40+i}:00",100,"old","Chip","1234","validation") for i in range(4)]
        db.executemany("INSERT INTO transactions VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
        db.execute("CREATE TABLE labels(answer TEXT)")
        db.execute("INSERT INTO labels VALUES ('secret-answer-not-for-agent')")
    manifest = export_case(tmp_path / "case", source_database=source, expected_source_sha256=sha256_file(source),
        transaction_id="current", alert={"transaction_id": "current", "score": .99, "detector": "test-only"},
        coverage_start=coverage_start, coverage_end="2020-01-01T00:00:00", full_source_scanned=scanned,
        source_report_sha256="test-report")
    return CaseTools(manifest), manifest, source


def query(tools, name, arguments=None):
    result = tools.dispatch(name, json.dumps(arguments or {}))
    assert result["ok"], result
    return result["data"]


def finding(check, assessment, *ids):
    return json.dumps({"findings": [{"check": check, "assessment": assessment, "evidence_ids": list(ids)}]})


def test_projection_physically_removes_labels_future_peers_and_other_users(tmp_path):
    tools, manifest, _ = build(tmp_path)
    with sqlite3.connect(manifest.parent / "facts.sqlite") as db:
        assert {row[0] for row in db.execute("SELECT transaction_id FROM transactions")} == {"current", "edge", "before", "negative"}
        with pytest.raises(sqlite3.OperationalError):
            db.execute("SELECT * FROM labels")
    result = run_investigation(OfflineInvestigationClient(), tools, client_mode="scripted_offline")
    assert "secret-answer-not-for-agent" not in json.dumps(result)
    assert "secret-future" not in json.dumps(result)


def test_exact_window_start_current_exclusion_and_signed_money(tmp_path):
    tools, _, _ = build(tmp_path)
    row = query(tools, "summarize_history", {"lookback_hours": 1})
    assert row["prior_count"] == 2 and row["amount_sum_cents"] == 500
    assert row["positive_amount_max_cents"] == 1000
    assert row["complete_within_source"] is True
    month = query(tools, "summarize_history", {"lookback_hours": 720})
    assert month["prior_count"] == 3 and month["amount_sum_cents"] == 2500
    assert month["positive_amount_max_cents"] == 2000


def test_aggregates_not_limited_by_preview(tmp_path):
    tools, _, _ = build(tmp_path, frequent=True)
    data = query(tools, "summarize_history", {"lookback_hours": 1})
    assert data["prior_count"] == 6 and len(data["preview"]) == 3
    assert data["preview_truncated"] and data["amount_sum_cents"] == 900


@pytest.mark.parametrize("raw", ['{"lookback_hours":true}', '{"lookback_hours":1.0}', '{"lookback_hours":NaN}',
    '{"lookback_hours":1,"lookback_hours":720}', '[]', '{"lookback_hours":"1;DROP TABLE transactions"}',
    '{"lookback_hours":1,"as_of":"2099-01-01"}', '{"lookback_hours":1,"user_id":"u1"}', '{"path":".env"}'])
def test_argument_bypass_and_malformed_json_rejected(tmp_path, raw):
    tools, _, _ = build(tmp_path)
    assert tools.dispatch("summarize_history", raw)["ok"] is False
    assert not tools.evidence


def test_no_file_sql_or_label_tool(tmp_path):
    tools, _, _ = build(tmp_path)
    names = {row["function"]["name"] for row in tools.schemas}
    assert names == {"get_alert_transaction", "summarize_history", "query_target_merchant"}
    assert not tools.dispatch("read_file", '{"path":"evaluation-labels.sqlite"}')["ok"]
    assert not tools.dispatch("execute_sql", '{"sql":"SELECT * FROM labels"}')["ok"]


def test_target_parameters_cannot_change_case(tmp_path):
    tools, _, _ = build(tmp_path)
    assert not tools.dispatch("get_alert_transaction", '{"user_id":"u1"}')["ok"]


def test_invalid_attempts_consume_budget_and_exhaustion_is_logged(tmp_path):
    _, manifest, _ = build(tmp_path)
    tools = CaseTools(manifest, max_calls=1)
    assert not tools.dispatch("unknown", "{}")["ok"]
    assert tools.dispatch("get_alert_transaction", "{}")["error_type"] == "ToolBudgetExceeded"
    assert tools.attempts == 2 and len(tools.trace) == 2 and not tools.evidence


def test_changed_projection_refused(tmp_path):
    _, manifest, _ = build(tmp_path)
    with sqlite3.connect(manifest.parent / "facts.sqlite") as db:
        db.execute("UPDATE transactions SET amount_cents=0")
    with pytest.raises(ValueError, match="changed"):
        CaseTools(manifest)


def test_final_test_target_not_exported(tmp_path):
    _, _, source = build(tmp_path)
    with pytest.raises(ValueError, match="2018"):
        export_case(tmp_path / "forbidden", source_database=source, expected_source_sha256=sha256_file(source),
            transaction_id="final-test", alert={"transaction_id":"final-test","score":.9},
            coverage_start="2017-12-01", coverage_end="2020-01-01", full_source_scanned=True, source_report_sha256="test")
    assert not (tmp_path / "forbidden").exists()


def test_normal_frequency_takes_amount_and_merchant_path(tmp_path):
    tools, _, _ = build(tmp_path)
    result = run_investigation(OfflineInvestigationClient(), tools, client_mode="scripted_offline")
    assert result["status"] == "completed_validated"
    assert result["client_requests"] == 5 and result["tool_attempts"] == 4
    assert [row["assessment"] for row in result["findings"]["findings"]] == ["not_supported", "supported", "supported"]
    assert [row["arguments"] for row in result["tool_trace"]] == [{}, {"lookback_hours":1}, {"lookback_hours":720}, {"lookback_hours":720}]


def test_report_displays_exact_source_units_without_float_money(tmp_path):
    tools, _, _ = build(tmp_path)
    result = run_investigation(OfflineInvestigationClient(), tools, client_mode="scripted_offline")
    text = result["report_markdown"]
    assert "本笔金额 100.00" in text and "最大值：20.00" in text
    assert "金额净和 25.00" in text and "币种未知" in text
    assert "覆盖完整" in text and "0.01 源单位" not in text


def test_frequent_path_queries_day_context_instead(tmp_path):
    tools, _, _ = build(tmp_path, frequent=True)
    result = run_investigation(OfflineInvestigationClient(), tools, client_mode="scripted_offline")
    assert result["status"] == "completed_validated" and result["tool_attempts"] == 3
    assert result["tool_trace"][-1]["arguments"] == {"lookback_hours":24}
    assert result["findings"]["findings"][0]["assessment"] == "supported"


@pytest.mark.parametrize("coverage_start,scanned", [("2018-02-01T11:30:00", True), ("2017-12-01", False)])
def test_incomplete_coverage_ends_with_gap_not_safety(tmp_path, coverage_start, scanned):
    tools, _, _ = build(tmp_path, coverage_start=coverage_start, scanned=scanned)
    result = run_investigation(OfflineInvestigationClient(), tools, client_mode="scripted_offline")
    assert result["status"] == "completed_validated" and result["tool_attempts"] == 2
    assert result["findings"]["findings"][0]["assessment"] == "insufficient_evidence"


def test_seen_merchant_and_negative_target_are_not_invented_refunds(tmp_path):
    tools, _, _ = build(tmp_path, merchant="old", amount=-100)
    result = run_investigation(OfflineInvestigationClient(), tools, client_mode="scripted_offline")
    statuses = {row["check"]: row["assessment"] for row in result["findings"]["findings"]}
    assert statuses["merchant_id_unseen_30d"] == "not_supported"
    assert statuses["amount_above_prior_positive_max_30d"] == "insufficient_evidence"


def test_registry_is_not_mutated_by_returned_result(tmp_path):
    tools, _, _ = build(tmp_path)
    query(tools, "get_alert_transaction")
    data = query(tools, "summarize_history", {"lookback_hours":1})
    data["prior_count"] = 1000
    assert tools.evidence[data["evidence_id"]]["prior_count"] == 2
    with pytest.raises(ValueError, match="assessment"):
        validate_findings(finding("high_prior_frequency_1h", "supported", data["evidence_id"]), tools.evidence)


@pytest.mark.parametrize("attack", ["fake_reference", "wrong_window", "fraud_verdict", "extra_free_text", "wrong_number_status"])
def test_unsupported_final_claims_rejected(tmp_path, attack):
    tools, _, _ = build(tmp_path)
    query(tools, "get_alert_transaction")
    data = query(tools, "summarize_history", {"lookback_hours":24 if attack=="wrong_window" else 1})
    body = json.loads(finding("high_prior_frequency_1h", "not_supported", data["evidence_id"]))
    if attack == "fake_reference": body["findings"][0]["evidence_ids"] = ["fabricated"]
    if attack == "fraud_verdict": body["findings"][0]["assessment"] = "confirmed_fraud"
    if attack == "extra_free_text": body["conclusion"] = "real loss is 100 dollars"
    if attack == "wrong_number_status": body["findings"][0]["assessment"] = "supported"
    with pytest.raises(ValueError):
        validate_findings(json.dumps(body), tools.evidence)


def test_no_tools_cannot_publish_successful_report(tmp_path):
    tools, _, _ = build(tmp_path)
    client = FakeLLMClient([{"role":"assistant","content":finding("high_prior_frequency_1h", "supported", "fake")}])
    result = run_investigation(client, tools, client_mode="fake")
    assert result["status"] == "report_rejected" and result["report_markdown"] is None


def test_iteration_and_parallel_call_limits(tmp_path):
    tools, _, _ = build(tmp_path)
    result = run_investigation(OfflineInvestigationClient(), tools, client_mode="offline", max_iterations=1)
    assert result["status"] == "iteration_limit" and result["report_markdown"] is None
    tools2 = CaseTools(tmp_path / "case/manifest.json")
    call = make_tool_call_message("x", "get_alert_transaction", {})
    call["tool_calls"] *= 4
    result = run_investigation(FakeLLMClient([call]), tools2, client_mode="fake")
    assert result["status"] == "model_or_protocol_error" and not tools2.evidence


def test_chat_completions_http_mock_uses_same_tools_without_network(tmp_path):
    tools, _, _ = build(tmp_path)
    policy = OfflineInvestigationClient()
    requests = []
    def handler(request):
        assert request.url.host == "api.siliconflow.cn"
        payload = json.loads(request.content)
        requests.append(payload)
        message = policy.chat(payload["messages"], payload["tools"])
        return httpx.Response(200, json={"choices":[{"message":message}]})
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        client = GLMClient("unit-test-placeholder", "Pro/zai-org/GLM-5.1",
            base_url="https://api.siliconflow.cn/v1/chat/completions", http_client=http)
        result = run_investigation(client, tools, client_mode="HTTP_MOCK_NOT_REAL_LLM")
    assert result["status"] == "completed_validated" and len(requests) == 5
    assert any(message["role"] == "tool" for message in requests[2]["messages"])


@pytest.mark.parametrize("scenario",["valid_repair","still_wrapped","false_assessment","tool_request","budget_exhausted","semantic_error"])
def test_one_format_repair_preserves_evidence_checks_and_total_budget(tmp_path,scenario):
    tools, _, _ = build(tmp_path)
    scripted = OfflineInvestigationClient()
    class WrappedClient:
        def __init__(self):
            self.final = None
            self.requests = 0
        def chat(self,messages,tools=None):
            self.requests += 1
            if self.final is not None:
                assert tools == []
                if scenario == "tool_request":
                    return make_tool_call_message("repair-tool","get_alert_transaction",{})
                if scenario == "still_wrapped":
                    return {"role":"assistant","content":"```json\n"+self.final+"\n```"}
                if scenario == "false_assessment":
                    body = json.loads(self.final)
                    body["findings"][0]["assessment"] = "supported"
                    return {"role":"assistant","content":json.dumps(body)}
                return {"role":"assistant","content":self.final}
            message = scripted.chat(messages,tools)
            if not message.get("tool_calls"):
                self.final = message["content"]
                if scenario == "semantic_error":
                    body = json.loads(self.final)
                    body["findings"][0]["assessment"] = "supported"
                    message["content"] = json.dumps(body)
                else:
                    message["content"] = "Explanation\n```json\n"+self.final+"\n```"
            return message
    model = WrappedClient()
    result = run_investigation(model,tools,client_mode="fake-format-repair",
        max_iterations=5 if scenario=="budget_exhausted" else 6,allow_format_repair=True)
    assert result["client_requests"] == model.requests <= 6
    assert result["tool_attempts"] == 4
    if scenario == "valid_repair":
        assert result["status"] == "completed_validated"
        assert result["format_repair"]["status"] == "validated"
        assert "Explanation" in result["messages"][-3]["content"]  # original failure retained
    else:
        assert result["report_markdown"] is None
        assert result["status"] != "completed_validated"
    assert result["format_repair"]["attempted"] == (scenario not in {"budget_exhausted","semantic_error"})
