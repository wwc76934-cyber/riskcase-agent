import json

from app.semantic.cross_context import fact_ok, parse_cross, quote_ok
from app.semantic.policy import decide, is_intercept

STATEMENT = "我昨晚一直在家，手机在床头。"
FACTS = {"device_used": "本人常用手机，指纹验证", "txn_city": "上海", "target_txn_time": "2023-05-12 20:15:00"}


def reply(signals, contradicted=True):
    return json.dumps(dict(claim_contradicted=contradicted, confidence=0.9, reasoning="x", signals=signals))


def test_both_anchors_must_hold():
    good = dict(name="alibi_telemetry_contradiction", quote="手机在床头", fact_key="device_used", fact_value="本人常用手机，指纹验证")
    r = parse_cross(reply([good]), STATEMENT, FACTS)
    assert r["ok"] and [s["name"] for s in r["signals"]] == ["alibi_telemetry_contradiction"] and r["dual_dropped"] == 0
    invented_key = dict(good, fact_key="bluetooth_peer")
    invented_value = dict(good, fact_value="完全不同的值")
    invented_quote = dict(good, quote="我整晚没出门也没看手机")
    for bad in (invented_key, invented_value, invented_quote):
        r = parse_cross(reply([bad]), STATEMENT, FACTS)
        assert r["signals"] == [], bad


def test_dropped_signals_cannot_raise_the_action():
    r = parse_cross(reply([dict(name="alibi_telemetry_contradiction", quote="手机在床头", fact_key="nope", fact_value="x")]), STATEMENT, FACTS)
    assert not is_intercept(decide([s["name"] for s in r["signals"]]))


def test_multi_turn_shift_needs_two_different_quotes():
    text = "T0：卡从没借过人。\nT1：想起来了，上个月借给表弟用过。"
    one = dict(name="multi_turn_narrative_shift", quote="借给表弟用过", quote_earlier="")
    same = dict(one, quote_earlier="借给表弟用过")
    both = dict(one, quote_earlier="卡从没借过人")
    assert parse_cross(reply([one]), text, {})["signals"] == []
    assert parse_cross(reply([same]), text, {})["signals"] == []
    assert [s["name"] for s in parse_cross(reply([both]), text, {})["signals"]] == ["multi_turn_narrative_shift"]


def test_cross_signals_are_core_for_the_policy():
    assert is_intercept(decide(["temporal_scope_mismatch"]))
    assert is_intercept(decide(["alibi_telemetry_contradiction", "official_verification_offered"]) ) is False


def test_matching_ignores_spacing_and_case():
    assert quote_ok("手机 在床头", STATEMENT)
    assert fact_ok("txn_city", "上海", FACTS) and not fact_ok("txn_city", "北京", FACTS) and not fact_ok("missing", "x", FACTS)
