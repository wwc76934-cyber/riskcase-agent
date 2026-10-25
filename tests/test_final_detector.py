"""Synthetic final-period fixtures only. Never opens the real 2019 dataset."""

import json
from pathlib import Path
import sqlite3

import numpy as np
import pytest
from sklearn.metrics import average_precision_score

from app.risk.final_detector import audit, evaluate, freeze, freeze_predictions, independent_ap, load
from app.risk.history import sha256_file
from app.risk.ml_models import MODEL_COLUMNS


@pytest.fixture
def frozen(tmp_path):
    source = tmp_path / "data"; source.mkdir()
    labels = source / "dataset/evaluation-labels.sqlite"; labels.parent.mkdir()
    feature_path = source / "features.sqlite"
    columns = list(dict.fromkeys(("transaction_id", "user_id", "event_time", "split", "history_30d_complete", *MODEL_COLUMNS)))
    with sqlite3.connect(feature_path) as db:
        db.execute("CREATE TABLE features(" + ",".join(c + (" TEXT" if c in ("transaction_id", "user_id", "event_time", "split", "channel", "mcc") else " INTEGER") for c in columns) + ")")
        for i in range(30):
            row = {c: 1 for c in columns}
            row.update(transaction_id=str(i), user_id="fixture-only", event_time=f"2019-01-{1+i//15:02d}T12:{i%15:02d}:00", split="test", history_30d_complete=1, amount_cents=i, channel="Chip", mcc="1234")
            db.execute("INSERT INTO features VALUES(" + ",".join("?" for _ in columns) + ")", tuple(row[c] for c in columns))
    with sqlite3.connect(labels) as db:
        db.execute("CREATE TABLE labels(transaction_id TEXT PRIMARY KEY,split TEXT,is_fraud INTEGER)")
        db.executemany("INSERT INTO labels VALUES (?,?,?)", ((str(i), "test", int(i%7 == 0)) for i in range(30)))
    cohort = source / "build-report.json"
    cohort.write_text(json.dumps({"artifact_sha256": {"dataset/evaluation-labels.sqlite": sha256_file(labels)}}), encoding="utf-8")
    manifest = source / "manifest.json"
    manifest.write_text(json.dumps({"cohort_report": str(cohort), "cohort_report_sha256": sha256_file(cohort), "features_sha256": sha256_file(feature_path), "rows_by_split": {"test": 30}}), encoding="utf-8")
    recipes = {"primary_model": "trial", "training_period": "2016-01-01/2018-01-01", "selection_period": "2018", "selection_rationale": "Synthetic protocol tests only; not real performance.",
               "models": [{"name": "hgb_v1", "kind": "amount"}, {"name": "trial", "kind": "amount"}]}
    root, directory = Path(__file__).resolve().parents[1], tmp_path / "final"
    freeze(root, directory, recipes, manifest)
    return root, directory


def predictor(model, directory, frame):
    values = frame["amount_cents"].to_numpy(dtype=float)
    return values if model["name"] == "hgb_v1" else -values


def test_no_labels_opened_before_prediction_freeze(frozen, monkeypatch):
    root, directory = frozen
    opened = []; connect = sqlite3.connect
    def observed(path, *a, **k):
        opened.append(str(path))
        return connect(path, *a, **k)
    monkeypatch.setattr(sqlite3, "connect", observed)
    manifest = freeze_predictions(root, directory, predictor=predictor)
    assert manifest["labels_opened"] is False
    assert not any("evaluation-labels" in p for p in opened)
    assert not (directory / "outcomes-opened.json").exists()
    with pytest.raises(ValueError, match="already_frozen"): freeze_predictions(root, directory, predictor=predictor)


def test_once_only_opening_and_independent_audit(frozen):
    root, directory = frozen
    freeze_predictions(root, directory, predictor=predictor)
    report = evaluate(root, directory)
    assert report == audit(directory)
    assert report["primary_model_frozen_before_test"] == "trial"
    assert report["rows"] == 30 and report["positives"] == 5
    assert report["strategies"]["trial"]["daily_review"]["10"]["reviews"] == 20
    assert report["strategies"]["trial"]["independent_ap_verified"]
    assert report["selection_after_final_test"] is False and not report["deployed"]
    with pytest.raises(FileExistsError): evaluate(root, directory)


def test_predictions_and_model_contract_tampering_fail(frozen):
    root, directory = frozen
    freeze_predictions(root, directory, predictor=predictor)
    with sqlite3.connect(directory / "predictions.sqlite") as db: db.execute("UPDATE scores SET score=42 WHERE transaction_id='1'")
    with pytest.raises(ValueError, match="not_frozen"): evaluate(root, directory)


def test_outcomes_cannot_be_replaced_before_opening(frozen):
    root, directory = frozen
    freeze_predictions(root, directory, predictor=predictor)
    contract = json.loads((directory / "contract.json").read_text(encoding="utf-8"))
    with sqlite3.connect(contract["labels"]) as db: db.execute("UPDATE labels SET is_fraud=0")
    with pytest.raises(ValueError, match="outcomes_changed"): evaluate(root, directory)


def test_nonfinite_inference_fails_before_any_labels(frozen):
    root, directory = frozen
    with pytest.raises(ValueError, match="invalid_final_predictions"):
        freeze_predictions(root, directory, predictor=lambda m, d, f: np.full(len(f), np.nan))
    assert not (directory / "outcomes-opened.json").exists()


def test_independent_ap_ties_and_zeros():
    y, scores = [1, 0, 1, 0, 1], [.9, .9, .2, .2, .1]
    assert independent_ap(y, scores) == pytest.approx(average_precision_score(y, scores))
    assert independent_ap([0, 0], [1., 2.]) is None
