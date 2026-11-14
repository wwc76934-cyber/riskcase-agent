import json

import pytest

from app.semantic.baselines import rules_predict
from app.semantic.benchmark import inject, obfuscate, plan, valid_turns
from app.semantic.evaluate import rates
from app.semantic.extractor import grounded, parse_extraction
from app.semantic.llm import Budget, BudgetExceeded, LLM, estimate_tokens, parse_json
from app.semantic.policy import decide

TEXT = "对方：请把验证码发给我\n客户：好的，我转到你的个人账户"


def test_plan_is_deterministic_and_splits_by_seed():
    a, b = plan("test", generator="g"), plan("test", generator="g")
    assert a == b and len(a) == 260
    train_seeds = {s["seed_idx"] for s in plan("train", generator="g")}
    test_seeds = {s["seed_idx"] for s in plan("test", generator="g")}
    assert not train_seeds & test_seeds
    assert all(s["truth_signals"] for s in a if s["kind"] != "ordinary")


def test_obfuscation_is_deterministic_and_changes_keywords():
    out = obfuscate(TEXT, "x")
    assert out == obfuscate(TEXT, "x") and out != TEXT and "验证码" not in out
    assert "对方" in out


def test_injection_adds_one_line():
    assert inject(TEXT).count("\n") == TEXT.count("\n") + 1


def test_quote_grounding_ignores_spacing_and_rejects_invention():
    assert grounded("验 证 码发给我", TEXT)
    assert not grounded("请转到安全账户", TEXT)


def test_parse_extraction_drops_ungrounded_signals():
    raw = json.dumps(dict(is_fraud=True, fraud_type="phishing", confidence=0.9, reasoning="x", signals=[
        dict(name="credential_request", quote="把验证码发给我"),
        dict(name="authority_claim", quote="我是公安局的"),
        dict(name="not_a_signal", quote="验证码")]))
    r = parse_extraction(raw, TEXT)
    assert r["ok"] and [s["name"] for s in r["signals"]] == ["credential_request"] and r["dropped_signals"] == 2
    assert parse_extraction("not json", TEXT) == dict(ok=False, error="parse")


def test_policy_tiers():
    assert decide(["offpath_transfer_request", "urgency_pressure"])["action"] == "HOLD_FOR_ANTI_SCAM_INTERVENTION"
    assert decide(["offpath_transfer_request"])["action"] == "STEP_UP_VERIFICATION"
    assert decide(["authority_claim"])["action"] == "RELEASE"
    assert decide(["offpath_transfer_request", "official_verification_offered"])["action"] == "RELEASE"


def test_rules_and_metrics():
    assert rules_predict("验证码 保证金 手续费")["is_fraud"]
    rows = [dict(label="fraud", kind="fraud", fraud_type="a", pred_fraud=True, pred_type="a"),
            dict(label="fraud", kind="fraud", fraud_type="a", pred_fraud=False, pred_type="none"),
            dict(label="legit", kind="near", fraud_type=None, pred_fraud=True, pred_type="a"),
            dict(label="legit", kind="ordinary", fraud_type=None, pred_fraud=False, pred_type="none")]
    r = rates(rows)
    assert r["fraud_recall"] == 0.5 and r["precision"] == 0.5 and r["fpr_near"] == 1.0 and r["fpr_ordinary"] == 0.0


def test_valid_turns_rejects_short_or_non_chinese():
    assert valid_turns(None) is None
    assert valid_turns(dict(turns=[dict(speaker="客户", text="hi")] * 5)) is None


def test_budget_refuses_before_spending_and_cache_is_free(tmp_path):
    budget = Budget(tmp_path / "l.sqlite", "0.0001")
    llm = object.__new__(LLM)
    llm.budget, llm.key = budget, "k" * 12
    with pytest.raises(BudgetExceeded):
        llm.chat("Qwen/Qwen3.8-27B", "s", "u" * 500, purpose="t")
    assert budget.spent() == 0
    budget.store("k", "{}", 1, 1)
    assert parse_json("```json\n{\"a\": 1}\n```") == {"a": 1} and estimate_tokens("你好") > 2
