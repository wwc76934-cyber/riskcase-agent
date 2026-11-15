import pytest

from app.evaluation.v4_heldout import candidates
from app.investigation.v3.semantic_probe import screen
from app.investigation.v3.trajectory import run_case
from tests.test_v3_source_access import start

PLAIN = ("h07_plain_denied", "h08_plain_authorized", "h09_plain_pair", "h10_plain_unavail_auth", "h11_plain_revoked")


def plain_cases():
    return [(s, r) for s, r in candidates() if s["case_id"].split("eval-")[1] in PLAIN]


def run(tmp_path, snapshot, condition, **kw):
    (tmp_path / condition).mkdir()
    _, _, source = start(tmp_path / condition, snapshot)
    return run_case(source, condition, **kw)


def test_b5_needs_a_probe(tmp_path):
    snapshot, _ = plain_cases()[0]
    with pytest.raises(ValueError):
        run(tmp_path, snapshot, "B5", client_factory=lambda stage: None)


@pytest.mark.parametrize("index", range(5))
def test_plain_wording_routes_to_the_free_report_and_agrees_with_b0(tmp_path, index):
    snapshot, _ = plain_cases()[index]
    calls = []
    def probe(text):
        calls.append(text)
        return dict(complex=screen(text), flags=[], used_model=False, cny="0")
    b5 = run(tmp_path, snapshot, "B5", client_factory=lambda stage: None, probe=probe)
    b0 = run(tmp_path, snapshot, "B0")
    assert [p["plan"]["route"] for p in b5["phases"] if p["plan"]] == ["B0"] * 3 or all(
        p["plan"]["route"] == "B0" for p in b5["phases"] if p["stage"] > 0)
    for new, old in zip(b5["phases"], b0["phases"]):
        assert new["report"]["source_statement"]["value"] == old["report"]["source_statement"]["value"]
        assert new["report"]["proposed_disposition"] == old["report"]["proposed_disposition"]
    assert calls, "the probe was called with customer text"


def test_semantic_screen_flags_indirect_wording_and_passes_plain_wording():
    assert screen("这笔是孩子拿着我手机点出去的，我当时不知情")
    assert screen("我对着订单截图想起来了，之前报错了")
    assert not screen("我没有同意 P，我同意的是 Q。")
