from dataclasses import replace
from decimal import Decimal
import asyncio
import json

import httpx
import pytest

from app.evaluation.gate4_candidate import CandidateBudget, MAX_REQUESTS, UNKNOWN_HOLD, run_candidate, summarize_candidate
from app.evaluation.v31_query_development import callback_and_capture
from app.investigation.v3.delivery import request_for
from app.investigation.v3.gate4_provider import Gate4Policy, Gate4ProviderClient
from app.llm.cost_ledger import BudgetExceeded
from app.llm.glm_client import LLMError
from tests.test_v31_query_study import ScriptedClient
from tests.test_v3_native_planner import read_call


@pytest.fixture(params=[
    ("siliconflow-cn", "Qwen/Qwen3.8-27B"),
    ("ark-standard-cn", "doubao-seed-2-1-lite-260915"),
])
def policy(request):
    return Gate4Policy(*request.param, "json_schema", "3", "12",
                       "https://example.com/test-prices", "2026-11-10")


def budget_for(tmp_path, policy):
    cap = policy.estimate(policy.max_input_bytes + 4096, policy.max_output_tokens) * MAX_REQUESTS + UNKNOWN_HOLD
    return CandidateBudget(tmp_path / "ledger.sqlite", policy=policy, scope="mock", baseline=0, cap=cap)


def response(policy, message, *, metered=True):
    data = {"model": policy.model, "id": "mock-only",
            "choices": [{"finish_reason": "stop", "message": {"role": "assistant", **message}}]}
    if metered:
        data["usage"] = {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110}
    return httpx.Response(200, json=data)


def test_full_development_mcp_trajectory_uses_provider_specific_wire(policy, tmp_path):
    snapshot, reference = callback_and_capture(callback_available=False)
    wire = []

    def mock(request):
        assert str(request.url) == policy.endpoint
        packet = json.loads(request.content)
        wire.append(packet)
        assert packet["model"] == policy.model
        if policy.provider == "siliconflow-cn":
            assert packet["enable_thinking"] is False and "thinking" not in packet
        else:
            assert packet["thinking"] == {"type": "disabled"} and "enable_thinking" not in packet
        messages, tools = packet["messages"], packet.get("tools")
        if not tools:
            assert packet["response_format"]["json_schema"]["schema"] == json.loads(messages[0]["content"])["schema"]
            return response(policy, ScriptedClient().chat(messages))
        assert "response_format" not in packet
        context = json.loads(messages[1]["content"])
        catalog = context["catalog"]
        allowed = set(tools[0]["function"]["parameters"]["properties"]["source_ids"]["items"]["enum"])
        sources = {row["channel_ref"]: row["source_id"] for row in catalog["sources"] if row["source_id"] in allowed}
        trace = context["planning_trace"]
        if catalog["stage"] != 1:
            message = read_call(sorted(allowed)[:1])
        elif not trace:
            message = read_call([sources["routing"], sources["relay"]])
        elif len(trace) == 1:
            message = read_call([sources["registered-callback"]])
        elif len(trace) == 2:
            assert list(trace[-1]["query_outcomes"].values()) == ["unavailable"]
            message = read_call([sources["app-attestation"]])
        else:
            message = {"content": "Ready"}
        return response(policy, message)

    budget = budget_for(tmp_path, policy)
    budget.start()
    with pytest.raises(ValueError, match="already started"):
        budget.start()
    with httpx.Client(transport=httpx.MockTransport(mock)) as http:
        result = asyncio.run(run_candidate(tmp_path / "run", snapshot, policy, budget, http, "mock-credential",
                                           evidence_kind="scripted_protocol_probe"))
    assert result["source_transport"] == "official_mcp_stdio"
    assert len(result["phases"]) == 3 and all(p["report"] for p in result["phases"]), result
    assert len(wire) <= MAX_REQUESTS
    assert any(p.get("response_format") for p in wire) and any(p.get("tools") for p in wire)
    trace = result["phases"][1]["plan"]["trace"]
    assert [step["source_ids"][0] for step in trace[1:]] == reference["required_T1_queries"]
    assert reference["required_T2_original"] in result["phases"][2]["source_state"]["acquired_source_ids"]
    assert len(list((tmp_path / "run").rglob("provider-*.json"))) == len(wire)
    score = summarize_candidate(snapshot, reference, result, budget.summary(budget.prefix))
    assert score["complete_delivery"] and score["model_requests"] == len(wire)
    assert score["phases"][1]["replanned_after_unavailable"]
    assert score["evidence_kind"] == "scripted_protocol_probe"


@pytest.mark.parametrize("scenario", ["401", "timeout", "empty", "bad_schema", "model_changed", "missing_usage"])
def test_adapter_preserves_outcome_and_stops_unknown(policy, tmp_path, scenario):
    budget = budget_for(tmp_path, policy)
    request = request_for("extract_and_report",
                          {"type": "object", "additionalProperties": False}, {})
    called = []

    def mock(http_request):
        called.append(http_request)
        if scenario == "401":
            return httpx.Response(401, text="mock-credential")
        if scenario == "timeout":
            raise httpx.ReadTimeout("mock-credential")
        if scenario == "model_changed":
            return response(replace(policy, model=("Pro/zai-org/GLM-5.1" if policy.provider == "siliconflow-cn"
                                                   else "doubao-seed-2-1-pro-260915")), {"content": "{}"})
        return response(policy, {"content": "" if scenario == "empty" else '{"wrong":true}'},
                        metered=scenario != "missing_usage")

    with httpx.Client(transport=httpx.MockTransport(mock)) as http:
        client = Gate4ProviderClient(policy, "mock-credential", budget=budget, http=http)
        if scenario in ("empty", "bad_schema"):
            message = client.chat(**request)
            if scenario == "empty":
                assert message["known_provider_outcome"] == "empty_content"
            else:
                from app.investigation.v3.output import checked, OutputFailure
                with pytest.raises(OutputFailure):
                    checked(json.loads(message["content"]), {"type": "object", "additionalProperties": False})
        else:
            with pytest.raises(LLMError):
                client.chat(**request)
            with pytest.raises(BudgetExceeded):
                client.chat(**request)
            assert Decimal(budget.summary(budget.prefix)["run_committed_estimate_cny"]) == UNKNOWN_HOLD
        assert len(called) == 1
        assert "mock-credential" not in json.dumps(client.snapshot())
