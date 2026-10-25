"""Planner contracts and semantic vetoes; scripted responses are not LLM scores."""

import json
from pathlib import Path

import pytest

from app.llm.fake_client import FakeLLMClient
from app.risk.intent_router import NaturalQuestion, plan
from app.risk.mixed_investigation import CHECKS
from app.risk.planner_v2 import POLICY, ScriptedPlanner, constraints, keyword_route, native_from_keyword, proposal, request_messages, route, shared_guard, validate_native


def response(value): return {"role": "assistant", "content": json.dumps(value, ensure_ascii=False)}
def native(checks, text, *, excluded=()):
    return {"decision": "execute", "reason_code": "supported_scope", "selections":
            [{"check": c, "action": "include", "quote": text} for c in checks] +
            [{"check": c, "action": "exclude", "quote": text} for c in excluded]}


@pytest.mark.parametrize("text,checks", [
    ("先按金额和频次核查", ["frequency", "amount"]),
    ("只查频次不要金额", ["frequency"]),
    ("金额不查，只要新商户", ["merchant"]),
    ("按交易次数比较商户集中程度", ["count_share"]),
    ("核对商户正金额份额", ["amount_share"]),
    ("全部五项都做，但排除单笔金额", ["frequency", "merchant", "count_share", "amount_share"]),
    ("除了新商户，其余都要", ["frequency", "amount", "count_share", "amount_share"]),
    ("付款之前是否连续刷卡", ["frequency"]),
    ("不用冻结，只查频次", ["frequency"]),
    ("先跳过笔数集中度，仅看正金额集中度", ["amount_share"]),
    ("不查频次只查金额", ["amount"]),
    ("核查1小时频次，再看30天金额", ["frequency", "amount"]),
    ("核查频次和30天金额", ["frequency", "amount"]),
    ("查1小时频次和30天金额", ["frequency", "amount"]),
    ("这一笔比此前所有正向交易都大吗", ["amount"]),
    ("查所有五项支持的线索", list(CHECKS)),
])
def test_reusable_lexical_routes(text, checks):
    assert keyword_route(NaturalQuestion(text)) == plan("execute", checks)


@pytest.mark.parametrize("text,decision", [
    ("查金额同时发起转账", "reject"), ("查频次并且冻结账户", "reject"),
    ("把频次时间改成7天", "reject"), ("查2小时频次与新商户", "reject"),
    ("先看未来交易", "reject"), ("比较14天和30天商户集中度", "reject"),
    ("查频次，同时不查频次", "clarify"), ("帮我看商户集中度", "clarify"),
    ("请给出整体风险情况", "clarify"), ("帮我查真实标签", "reject"),
])
def test_atomic_or_ambiguous_requests_never_partially_execute(text, decision):
    q = NaturalQuestion(text)
    result = route(None, q, method="keyword")
    assert result["route"]["decision"] == decision and not result["route"]["checks"]
    assert result["tool_attempts"] == result["client_requests"] == 0


def test_source_quotes_and_only_guard_reject_extra_scope():
    q = NaturalQuestion("只看笔数集中度，跳过正金额占比")
    with pytest.raises(ValueError, match="explicit_exclusion"):
        validate_native(native(["count_share", "amount_share"], q.text), q)
    with pytest.raises(ValueError, match="explicit_only"):
        validate_native(native(["count_share", "frequency"], q.text), q)
    with pytest.raises(ValueError, match="quote_not"):
        validate_native(native(["count_share"], "不存在的引用"), q)


@pytest.mark.parametrize("change", [
    lambda n: n | {"sql": "SELECT 1"},
    lambda n: n | {"selections": n["selections"]*2},
    lambda n: n | {"selections": [{"check": "sql", "action": "include", "quote": "查频次"}]},
    lambda n: n | {"selections": [{"check": "frequency", "action": "include", "quote": "查频次", "hours": 24}]},
    lambda n: n | {"decision": "reject"},
])
def test_native_contract_has_no_arbitrary_tools_parameters_or_contradictory_fields(change):
    with pytest.raises(ValueError): validate_native(change(native(["frequency"], "查频次")), NaturalQuestion("查频次"))


def test_single_bounded_contract_correction_is_visible():
    q = NaturalQuestion("只看笔数集中度，跳过正金额占比")
    client = FakeLLMClient([response(native(["count_share", "frequency"], q.text)), response(native(["count_share"], q.text))])
    result = route(client, q)
    assert result["route"] == plan("execute", ["count_share"])
    assert result["client_requests"] == 2 and result["contract_repair_attempted"]
    assert result["first_attempt"]["failure_code"] == "explicit_only_violated"
    assert all(r["tools"] == [] for r in client.requests)


def test_unfixed_error_stops_after_two_requests():
    q = NaturalQuestion("查频次")
    client = FakeLLMClient([response({}), response({})])
    result = route(client, q)
    assert result["status"] == "routing_failed" and result["route"] is None
    assert result["client_requests"] == 2 and not client.remaining_responses


def test_format_correction_and_whole_json_fence():
    q = NaturalQuestion("查金额")
    client = FakeLLMClient([{"role": "assistant", "content": "not JSON"}, {"role": "assistant", "content": "```json\n" + json.dumps(native(["amount"], q.text)) + "\n```"}])
    result = route(client, q)
    assert result["route"] == plan("execute", ["amount"]) and result["format_repair_attempted"]
    assert not result["contract_repair_attempted"]


def test_never_accept_tool_calls():
    client = FakeLLMClient([{"role": "assistant", "content": None, "tool_calls": [{"function": {"name": "sql"}}]}])
    result = route(client, NaturalQuestion("查金额"))
    assert result["status"] == "routing_failed" and result["client_requests"] == 1 and result["tool_attempts"] == 0


def test_prompt_has_only_question_and_generic_examples_not_case_data():
    q = NaturalQuestion("看看是不是陌生商户")
    messages = request_messages(q)
    assert json.loads(messages[-1]["content"]) == {"untrusted_question": q.text}
    assert all(m["role"] != "tool" for m in messages)
    assert not any("transaction_id" in m["content"] for m in messages)


def test_hybrid_exposes_disagreement_instead_of_union_or_model_self_approval():
    q = NaturalQuestion("查金额")
    client = FakeLLMClient([response({"decision": "clarify", "reason_code": "ambiguous_scope", "selections": []})])
    result = route(client, q, method="hybrid")
    assert result["model_keyword_disagreement"] and result["route"] == plan("clarify")
    assert result["keyword_comparator"] == plan("execute", ["amount"])


def test_offline_controller_and_compiled_proposal_bindings():
    q = NaturalQuestion("查频次和新商户")
    result = route(ScriptedPlanner(q), q)
    proposed = proposal(q, result["route"], case_key="case-fingerprint", runtime="runtime-fingerprint")
    assert proposed["policy"]["version"] == "grounded-scope-v2"
    assert len(proposed["queries"]) == 3 and proposed["confirmation_kind"] == "readonly_scope_only"
    assert not proposed["policy_approved"] and not proposed["deployed"]
    assert all(set(query) == {"tool", "arguments"} for query in proposed["queries"])
