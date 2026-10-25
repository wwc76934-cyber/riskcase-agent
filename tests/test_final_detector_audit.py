"""Independent adversarial audit tests using tiny self-authored fixtures only.

Never calls evaluate() or touches the real final-period dataset. Consistently
re-sealed derived artifacts emulate a producer bug, so a hash-only auditor is
not mistaken for an independent source/ordering check.
"""

import json
from pathlib import Path
import sqlite3

import numpy as np
import pytest
from sklearn.metrics import average_precision_score

from app.risk.final_detector import audit, freeze_predictions, independent_ap
from app.risk.history import sha256_file
from tests.test_final_detector import frozen, predictor


def write_json(path, value):
    Path(path).write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def prepared(frozen):
    root, directory = frozen
    freeze_predictions(root, directory, predictor=predictor)
    write_json(directory / "outcomes-opened.json", {
        "opened_at": "2026-10-22T00:00:00+00:00",
        "predictions_manifest_sha256": sha256_file(directory / "predictions.json"),
        "no_further_selection": True})
    return root, directory


def reseal_derived(directory):
    path = directory / "predictions.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["predictions_sha256"] = sha256_file(directory / "predictions.sqlite")
    write_json(path, manifest)
    marker = json.loads((directory / "outcomes-opened.json").read_text(encoding="utf-8"))
    marker["predictions_manifest_sha256"] = sha256_file(path)
    write_json(directory / "outcomes-opened.json", marker)


@pytest.mark.parametrize("column,value", [("channel", "CORRUPTED"), ("amount_cents", 999999), ("user_id", "wrong-user")])
def test_resealed_prediction_metadata_must_match_frozen_feature_source(frozen, column, value):
    _, directory = prepared(frozen)
    with sqlite3.connect(directory / "predictions.sqlite") as db:
        db.execute(f"UPDATE facts SET {column}=? WHERE transaction_id='0'", (value,))
    reseal_derived(directory)
    with pytest.raises(ValueError): audit(directory)


def test_source_feature_file_is_checked_again_during_audit(frozen):
    _, directory = prepared(frozen)
    contract = json.loads((directory / "contract.json").read_text(encoding="utf-8"))
    with sqlite3.connect(contract["features"]) as db:
        db.execute("UPDATE features SET amount_cents=amount_cents+1 WHERE transaction_id='0'")
    with pytest.raises(ValueError): audit(directory)


@pytest.mark.parametrize("column,value", [("day", "1999-01-01"), ("day_rank", 999)])
def test_resealed_review_day_and_rank_are_independently_verified(frozen, column, value):
    _, directory = prepared(frozen)
    with sqlite3.connect(directory / "predictions.sqlite") as db:
        changed = db.execute(f"UPDATE reviews SET {column}=? WHERE model='trial' AND budget=10 AND transaction_id='0'", (value,)).rowcount
    assert changed == 1
    reseal_derived(directory)
    with pytest.raises(ValueError): audit(directory)


@pytest.mark.parametrize("marker", [{}, {"predictions_manifest_sha256": "0"*64, "no_further_selection": True},
                                  {"predictions_manifest_sha256": "PLACEHOLDER", "no_further_selection": False}])
def test_opening_marker_must_be_bound_and_no_further_selection(frozen, marker):
    _, directory = prepared(frozen)
    if marker.get("predictions_manifest_sha256") == "PLACEHOLDER": marker = marker | {"predictions_manifest_sha256": sha256_file(directory / "predictions.json")}
    write_json(directory / "outcomes-opened.json", marker)
    with pytest.raises(ValueError): audit(directory)


def test_passed_contract_cannot_silently_override_frozen_contract(frozen):
    _, directory = prepared(frozen)
    contract = json.loads((directory / "contract.json").read_text(encoding="utf-8"))
    contract["primary_model"] = "not-the-frozen-primary"
    with pytest.raises(ValueError): audit(directory, contract=contract)


def test_self_authored_fixture_has_expected_same_budget_counts(frozen):
    _, directory = prepared(frozen)
    report = audit(directory)
    # Two days of 15 records each; outcomes at 0,7,14,21,28. These totals are
    # derived by hand from the fixture, not by the production ranking helper.
    baseline = report["strategies"]["hgb_v1"]["daily_review"]
    trial = report["strategies"]["trial"]["daily_review"]
    assert baseline["10"]["reviews"] == trial["10"]["reviews"] == 20
    assert baseline["10"]["true_positives"] == 4
    assert trial["10"]["true_positives"] == 3
    assert baseline["10"]["false_positives"] == 16 and trial["10"]["false_positives"] == 17
    assert baseline["10"]["false_negatives"] == 1 and trial["10"]["false_negatives"] == 2
    assert baseline["20"]["reviews"] == trial["20"]["reviews"] == 30  # Capacity is an upper bound.
    for value in (baseline["10"], trial["10"]):
        assert value["groups"]["channel"]["Chip"]["true_positives"] == value["true_positives"]
        assert value["groups"]["month"]["2019-01"]["reviews"] == value["reviews"]


def test_independent_ap_with_random_tied_scores_and_degenerate_outcomes():
    generator = np.random.default_rng(721)
    for length in (1, 2, 17, 129):
        for _ in range(8):
            y = generator.integers(0, 2, length)
            scores = generator.integers(-3, 4, length).astype(float)
            if y.sum(): assert independent_ap(y, scores) == pytest.approx(average_precision_score(y, scores), abs=1e-12)
            else: assert independent_ap(y, scores) is None
    assert independent_ap([1, 1, 1], [2., 2., -1.]) == 1.
