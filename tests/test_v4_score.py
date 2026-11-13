from copy import deepcopy

from app.evaluation.v31_query_benchmark import candidates
from app.evaluation.v4_score import action_metrics, cost_efficiency, score_stage_v4, summarize
from app.investigation.v3.trajectory import run_case
from tests.test_v3_source_access import start

TRUTH = dict(allowed_actions=["a", "b"], required_action_groups=[["a"], ["b", "c"]])


def test_benign_extra_lowers_precision_but_is_not_unsafe():
    m = action_metrics(["a", "b", "extra"], TRUTH)
    assert m["recall"] == 1.0 and m["unsafe"] == [] and m["benign_extra"] == ["extra"]
    assert m["precision"] == 2 / 2.5


def test_unsafe_action_is_reported_separately_from_benign_extra():
    m = action_metrics(["a", "freeze_account"], TRUTH)
    assert m["unsafe"] == ["freeze_account"] and m["benign_extra"] == []


def test_missing_required_group_lowers_recall():
    assert action_metrics(["a"], TRUTH)["recall"] == 0.5


def test_cost_efficiency_needs_mandatory_evidence():
    assert cost_efficiency(4, 10, True) == 0.6
    assert cost_efficiency(4, 10, False) == 0.0
    assert cost_efficiency(None, 10, True) is None


def test_extra_benign_action_fails_legacy_but_passes_v4_gate(tmp_path):
    for index, (snapshot, reference) in enumerate(candidates()):
        (tmp_path / str(index)).mkdir()
        _, _, source = start(tmp_path / str(index), snapshot)
        phase = run_case(source, "B0")["phases"][2]
        base = score_stage_v4(snapshot, reference, 2, phase["evidence"], phase["report"])
        if base["legacy"]["strict_author_reference_pass"]:
            break
    else:
        raise AssertionError("no passing B0 stage to perturb")
    report = deepcopy(phase["report"])
    extra = deepcopy(report["recommendations"][0])
    extra["action"] = "benign_extra_follow_up_not_in_reference"
    report["recommendations"].append(extra)
    scored = score_stage_v4(snapshot, reference, 2, phase["evidence"], report)
    assert not scored["legacy"]["strict_author_reference_pass"]
    assert scored["v4"]["business_safe_pass"]
    assert scored["v4"]["benign_extra_actions"] == ["benign_extra_follow_up_not_in_reference"]
    assert scored["v4"]["quality_components"]["action_precision"] < base["v4"]["quality_components"]["action_precision"]


def test_unsafe_action_fails_gate(tmp_path):
    snapshot, reference = next(candidates())
    _, _, source = start(tmp_path, snapshot)
    phase = run_case(source, "B0")["phases"][2]
    report = deepcopy(phase["report"])
    unsafe = deepcopy(report["recommendations"][0])
    unsafe["action"] = "freeze_account"
    report["recommendations"].append(unsafe)
    scored = score_stage_v4(snapshot, reference, 2, phase["evidence"], report)["v4"]
    assert not scored["business_safe_pass"] and not scored["gates"]["gate_no_dangerous_action"]


def test_summary_counts_both_views():
    rows = [dict(legacy_strict_exact_pass=False, business_safe_pass=True, quality_score=80.0,
                 gates=dict(g=True)),
            dict(legacy_strict_exact_pass=False, business_safe_pass=False, quality_score=40.0,
                 gates=dict(g=False))]
    s = summarize(rows)
    assert (s["legacy_strict_pass"], s["business_safe_pass"], s["mean_quality_score"]) == (0, 1, 60.0)
    assert s["gate_failures"] == {"g": 1}
