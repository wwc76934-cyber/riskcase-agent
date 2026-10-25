"""Temporal leakage boundaries and controlled comparison semantics."""

import json
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
pd = pytest.importorskip("pandas")
pytest.importorskip("sklearn")

from app.risk.ml_evaluation import evaluate_scores
from app.risk.temporal_experiment import fold_masks, make_variant


ROOT = Path(__file__).resolve().parents[1]
BASE = json.loads((ROOT / "config/ml-experiment-v1.json").read_text())
POLICY = json.loads((ROOT / "config/temporal-experiment-v1.json").read_text())


def frame():
    return pd.DataFrame({"transaction_id": ["a", "b", "c", "d"],
        "event_time": ["2016-12-31T23:59:59", "2017-01-01T00:00:00", "2017-06-30T23:59:59", "2017-07-01T00:00:00"],
        "split": ["train"]*4})


def test_half_open_time_boundaries_and_no_overlap():
    fit, evaluate = fold_masks(frame(), POLICY["folds"][0])
    assert fit.tolist() == [True, False, False, False]
    assert evaluate.tolist() == [False, True, True, False]


@pytest.mark.parametrize("change", [{"fit_end": "2017-02-01"}, {"evaluate_end": "2019-01-01"}, {"evaluate_end": "2016-01-01"}])
def test_invalid_time_scope_rejected(change):
    with pytest.raises(ValueError):
        fold_masks(frame(), {**POLICY["folds"][0], **change})


@pytest.mark.parametrize("split", ["validation", "test"])
def test_cannot_mix_original_partitions(split):
    data = frame()
    data.loc[0, "split"] = split
    with pytest.raises(ValueError):
        fold_masks(data, POLICY["folds"][0])


def test_zero_positive_window_is_unknown_not_zero_ap_or_recall():
    stats, queue = evaluate_scores(frame(), np.zeros(4, dtype=int), [.1,.2,.3,.4], expected_split="train", daily_budget=1)
    assert stats["average_precision"] is None
    assert stats["roc_auc"] is None
    assert stats["daily_review"]["recall"] is None
    assert stats["daily_review"]["false_positives"] == 4
    assert all("is_fraud" not in row for row in queue)


def test_expected_test_split_cannot_bypass_guard():
    with pytest.raises(ValueError):
        evaluate_scores(frame().assign(split="test"), [0,1,0,1], [.1,.2,.3,.4], expected_split="test")


def test_variants_change_declared_inputs_and_not_shared_policy():
    original = json.dumps(BASE, sort_keys=True)
    dropped = make_variant("tree_no_channel", BASE, POLICY)
    columns = [column for _, _, names in dropped.named_steps["preprocess"].transformers for column in names]
    assert "channel" not in columns and "mcc" in columns and len(columns) == 13
    assert len(dropped.named_steps["model"].categorical_features) == 13
    base = make_variant("tree_base", BASE, POLICY).named_steps["model"]
    unweighted = make_variant("tree_unweighted", BASE, POLICY).named_steps["model"]
    changed = [key for key, value in base.get_params().items() if value != unweighted.get_params()[key]]
    assert changed == ["class_weight"]
    regularized = make_variant("tree_stronger_regularization", BASE, POLICY).named_steps["model"]
    assert regularized.max_leaf_nodes == 7 and regularized.min_samples_leaf == 200
    assert json.dumps(BASE, sort_keys=True) == original


def test_zero_positive_fold_is_kept_in_protocol():
    assert [fold["name"] for fold in POLICY["folds"]] == ["2017-H1", "2017-H2"]
    with pytest.raises(ValueError):
        make_variant("unregistered-search", BASE, POLICY)
