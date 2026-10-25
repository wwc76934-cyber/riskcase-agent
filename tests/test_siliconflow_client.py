"""Provider boundary tests; MockTransport never contacts SiliconFlow."""

from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path

import httpx
import pytest

from app.cli import investigate_live
from app.llm.cost_ledger import BudgetExceeded, CostLedger
from app.llm.glm_client import LLMError
from app.llm.siliconflow_client import ENDPOINT, MODEL, SiliconFlowClient, usage_counts


ROOT = Path(__file__).resolve().parents[1]
KEY = "unit-test-secret-never-log"


def response(**changes):
    data = {"id":"test-response", "model":MODEL,
        "choices":[{"message":{"role":"assistant", "content":"ok", "reasoning_content":"private-reasoning"}, "finish_reason":"stop"}],
        "usage":{"prompt_tokens":100, "completion_tokens":20, "total_tokens":120,
            "completion_tokens_details":{"reasoning_tokens":10}}}
    data.update(changes)
    return data


def client(tmp_path, handler=None, **policy_changes):
    policy = json.loads((ROOT / "config/siliconflow-live-v1.json").read_text(encoding="utf-8"))
    policy.update(policy_changes)
    transport = httpx.MockTransport(handler or (lambda request: httpx.Response(200,json=response())))
    http = httpx.Client(transport=transport)
    instance = SiliconFlowClient(KEY,policy=policy,ledger=CostLedger(tmp_path / "ledger.sqlite"),
        run_id="test-run",http_client=http)
    return instance


def test_success_provider_contract_and_reasoning_not_saved(tmp_path):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200,json=response(id=KEY))
    model = client(tmp_path,handler)
    assert model.chat([{"role":"user","content":"investigate"}]) == {"role":"assistant","content":"ok"}
    request = requests[0]
    assert str(request.url) == ENDPOINT
    assert request.headers["authorization"] == "Bearer " + KEY
    payload = json.loads(request.content)
    assert payload["model"] == MODEL and payload["enable_thinking"] is False
    assert payload["max_tokens"] == 1024 and payload["thinking_budget"] == 128
    assert not payload["stream"] and KEY not in request.content.decode()
    audit = model.snapshot()
    assert KEY not in json.dumps(audit) and "private-reasoning" not in json.dumps(audit)
    # Reasoning tokens are included in completion usage, not charged twice.
    assert Decimal(audit["ledger"]["run_committed_estimate_cny"]) == Decimal("0.00136")


@pytest.mark.parametrize("status",[301,401,429,500,503])
def test_http_failure_no_retry_no_response_body_log(tmp_path,status):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status,text=KEY,headers={"location":"https://example.com/secret"})
    model = client(tmp_path,handler)
    with pytest.raises(LLMError,match=f"http_{status}"):
        model.chat([])
    assert len(calls) == 1
    audit = model.snapshot()
    assert audit["ledger"]["unknown_cost_requests"] == 1
    assert KEY not in json.dumps(audit)


@pytest.mark.parametrize("usage",[None,{}, {"prompt_tokens":True,"completion_tokens":0,"total_tokens":1},
    {"prompt_tokens":1,"completion_tokens":-1,"total_tokens":0},
    {"prompt_tokens":1,"completion_tokens":2,"total_tokens":4},
    {"prompt_tokens":1,"completion_tokens":2,"total_tokens":3,"completion_tokens_details":{"reasoning_tokens":3}}])
def test_invalid_usage_keeps_estimate_reserved(tmp_path,usage):
    model = client(tmp_path,lambda request: httpx.Response(200,json=response(usage=usage)))
    with pytest.raises(LLMError): model.chat([])
    audit = model.snapshot()["ledger"]
    assert audit["unknown_cost_requests"] == 1
    assert Decimal(audit["run_committed_estimate_cny"]) > 0


def test_transport_uncertainty_is_sanitized_and_reserved(tmp_path):
    def handler(request): raise httpx.ReadTimeout(KEY,request=request)
    model = client(tmp_path,handler)
    with pytest.raises(LLMError,match="transport_error_unknown_charge") as caught:
        model.chat([])
    assert KEY not in str(caught.value)
    assert model.snapshot()["ledger"]["unknown_cost_requests"] == 1


def test_truncated_response_records_usage_but_rejects_message(tmp_path):
    data = response()
    data["choices"][0]["finish_reason"] = "length"
    model = client(tmp_path,lambda request: httpx.Response(200,json=data))
    with pytest.raises(LLMError): model.chat([])
    assert model.snapshot()["ledger"]["unknown_cost_requests"] == 0


@pytest.mark.parametrize("body",[b"not json",b"[]",b"null"])
def test_malformed_response(tmp_path,body):
    model = client(tmp_path,lambda request: httpx.Response(200,content=body))
    with pytest.raises(LLMError): model.chat([])
    assert model.snapshot()["ledger"]["unknown_cost_requests"] == 1


def test_input_and_credential_rejection_happen_before_send(tmp_path):
    model = client(tmp_path,max_input_bytes=1000)
    for content in (KEY,"x"*1500):
        with pytest.raises(LLMError): model.chat([{"role":"user","content":content}])
    assert model.sent_requests == 0 and not model.snapshot()["ledger"]["entries"]


def test_response_cap_retains_uncertain_reservation(tmp_path):
    model = client(tmp_path,lambda request: httpx.Response(200,content=b"x"*1001),max_response_bytes=1000)
    with pytest.raises(LLMError,match="response_byte_limit"): model.chat([])
    assert model.snapshot()["ledger"]["unknown_cost_requests"] == 1


def test_deadline_blocks_before_request(tmp_path):
    model = client(tmp_path)
    model.clock = lambda: model.started+181
    with pytest.raises(LLMError,match="run_deadline"): model.chat([])
    assert model.sent_requests == 0


def test_request_limit_in_shared_ledger(tmp_path):
    model = client(tmp_path,max_requests=2)
    model.chat([])
    model.chat([])
    with pytest.raises(LLMError,match="request_limit"): model.chat([])
    assert model.sent_requests == 2


def test_known_usage_overrun_is_recorded_before_stop(tmp_path):
    data = response(usage={"prompt_tokens":100000,"completion_tokens":20,"total_tokens":100020})
    model = client(tmp_path,lambda request: httpx.Response(200,json=data))
    with pytest.raises(LLMError,match="usage_exceeded_heuristic_reservation"): model.chat([])
    audit = model.snapshot()["ledger"]
    assert audit["unknown_cost_requests"] == 0
    assert Decimal(audit["run_committed_estimate_cny"]) == Decimal("0.80056")


def test_project_budget_cannot_be_reset_with_new_run_id(tmp_path):
    ledger = CostLedger(tmp_path / "ledger.sqlite")
    identity = ledger.reserve("one","0.6",run_budget="1",project_budget="1",max_requests=6)
    ledger.settle(identity)  # unknown fee keeps the reservation
    with pytest.raises(BudgetExceeded):
        ledger.reserve("two","0.6",run_budget="1",project_budget="1",max_requests=6)
    with pytest.raises(ValueError,match="exactly once"): ledger.settle(identity)


@pytest.mark.parametrize("changes",[{"endpoint":"https://example.com"},{"model":"other"},
    {"automatic_retries":1},{"enable_thinking":True},{"run_budget_cny":"2"},{"project_budget_cny":"21"}])
def test_policy_cannot_expand_first_experiment(tmp_path,changes):
    with pytest.raises(ValueError): client(tmp_path,**changes)


def test_local_cli_missing_key_sends_nothing(monkeypatch,capsys):
    monkeypatch.delenv("SILICONFLOW_API_KEY",raising=False)
    monkeypatch.setattr(investigate_live,"load_saved_key",lambda:None)
    assert investigate_live.main(["--live"]) == 2
    assert "不要把密钥贴到聊天" in capsys.readouterr().err


def test_hidden_prompt_refuses_noninteractive_input(monkeypatch):
    monkeypatch.setattr(investigate_live.sys.stdin,"isatty",lambda:False)
    with pytest.raises(ValueError,match="interactive terminal"): investigate_live.local_key(True)
