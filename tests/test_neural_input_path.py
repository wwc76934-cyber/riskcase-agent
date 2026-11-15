"""Explicit rebuilt inputs must retain provenance and temporal isolation."""
import json
from pathlib import Path
import sqlite3

import pytest

pytest.importorskip("torch")
from app.cli.train_neural_v2 import load_development
from app.risk.history import sha256_file
from app.risk.ml_models import MODEL_COLUMNS


@pytest.fixture
def rebuilt(tmp_path):
    cohort = tmp_path / "rebuilt cohort"
    (cohort / "dataset").mkdir(parents=True)
    labels = cohort / "dataset/evaluation-labels.sqlite"
    features = tmp_path / "rebuilt features"
    features.mkdir()
    database = features / "features.sqlite"
    columns = ("transaction_id", "user_id", "event_time", "split", "history_30d_complete", *MODEL_COLUMNS)
    rows = []
    for split, year in (("train", 2017), ("validation", 2018), ("test", 2019)):
        for i in range(2):
            row = {c: 1 for c in columns}
            row.update(transaction_id=f"{split}-{i}", user_id="fixture-user",
                       event_time=f"{year}-06-01T12:00:00", split=split,
                       channel="Chip", mcc="5411")
            rows.append(row)
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE features(" + ",".join(columns) + ")")
        db.executemany("INSERT INTO features VALUES (" + ",".join("?" for _ in columns) + ")",
                       [tuple(row[c] for c in columns) for row in rows])
    with sqlite3.connect(labels) as db:
        db.execute("CREATE TABLE labels(transaction_id,split,is_fraud)")
        db.executemany("INSERT INTO labels VALUES (?,?,?)",
                       [(row["transaction_id"], row["split"],
                         "MUST_NOT_BE_LOADED" if row["split"] == "test" else i % 2)
                        for i, row in enumerate(rows)])
    report = cohort / "build-report.json"
    report.write_text(json.dumps({"partitions": {s: {"rows": 2, "fraud_rows": 1}
                       for s in ("train", "validation")},
                       "artifact_sha256": {"dataset/evaluation-labels.sqlite": sha256_file(labels)}}))
    manifest = features / "manifest.json"
    manifest.write_text(json.dumps({"cohort_report": str(report),
                        "cohort_report_sha256": sha256_file(report),
                        "features_sha256": sha256_file(database)}))
    return tmp_path, manifest, database, report, labels


def test_rebuilt_path_without_historical_directory_and_no_test_labels(rebuilt):
    root, manifest, *_ = rebuilt
    loaded, provenance = load_development(root, features_manifest=manifest)
    assert not (root / "data/features/features-b9692149f1c1").exists()
    assert [len(x) for x, _, _ in loaded] == [2, 2]
    assert [meta["split"].unique().tolist() for _, _, meta in loaded] == [["train"], ["validation"]]
    assert provenance["manifest_sha256"] == sha256_file(manifest)
    assert Path(provenance["manifest"]) == manifest.resolve()


@pytest.mark.parametrize("index", [2, 3, 4])
def test_rebuilt_artifact_mismatch_is_not_bypassed(rebuilt, index):
    root, manifest, *_ = rebuilt
    with rebuilt[index].open("ab") as handle:
        # Keep JSON parseable so this specifically exercises the checksum gate.
        handle.write(b" ")
    with pytest.raises(ValueError, match="provenance"):
        load_development(root, features_manifest=manifest)


def test_missing_explicit_manifest_does_not_fallback(rebuilt):
    root, *_ = rebuilt
    with pytest.raises(FileNotFoundError):
        load_development(root, features_manifest=root / "missing/manifest.json")


def test_future_rows_mislabeled_as_training_are_rejected(rebuilt):
    root, manifest, database, *_ = rebuilt
    with sqlite3.connect(database) as db:
        db.execute("UPDATE features SET event_time='2019-01-01T00:00:00' WHERE split='train'")
    body = json.loads(manifest.read_text())
    body["features_sha256"] = sha256_file(database)
    manifest.write_text(json.dumps(body))
    with pytest.raises(ValueError, match="Final rows forbidden"):
        load_development(root, features_manifest=manifest)
