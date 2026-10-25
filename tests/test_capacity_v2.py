"""Neural score alignment, frozen label-free allocation and unchanged real review state."""

from copy import deepcopy
import json
from pathlib import Path
import sqlite3

import numpy as np
import pytest

from app.risk.candidate_rules import FIELDS
from app.risk.capacity_v2 import POLICIES, allocate_neural_day, evaluate, freeze, load_protocol, materialize
from app.risk.history import sha256_file
from app.risk.review_store import digest
from app.risk.source_merchant_pack import write_json
from scripts.verify_capacity_v2 import verify


@pytest.fixture
def source(tmp_path):
    data = tmp_path / "source"; data.mkdir()
    features = data / "features.sqlite"
    labels = data / "dataset/evaluation-labels.sqlite"; labels.parent.mkdir()
    identities = [f"tx{i:02d}" for i in range(36)]
    with sqlite3.connect(features) as db:
        db.execute("CREATE TABLE features(transaction_id TEXT PRIMARY KEY,event_time TEXT,channel TEXT,split TEXT," + ",".join(f + " INTEGER" for f in sorted(FIELDS)) + ")")
        for i, identity in enumerate(identities):
            record = {f: 0 for f in FIELDS}
            record.update(amount_cents=100+i, history_30d_complete=1, has_history_30d=1, merchant_seen_30d=int(i%3 != 0))
            stamp = f"2018-01-{1+i//18:02d}T12:{i%18:02d}:00"
            db.execute("INSERT INTO features VALUES (" + ",".join("?" for _ in range(4+len(FIELDS))) + ")",
                       (identity, stamp, "Chip", "validation", *(record[f] for f in sorted(FIELDS))))
        record["amount_cents"] = 999999
        db.execute("INSERT INTO features VALUES (" + ",".join("?" for _ in range(4+len(FIELDS))) + ")",
                   ("forbidden-final-row", "2019-01-01T00:00:00", "Chip", "test", *(record[f] for f in sorted(FIELDS))))
    with sqlite3.connect(labels) as db:
        db.execute("CREATE TABLE labels(transaction_id TEXT PRIMARY KEY,is_fraud INTEGER,split TEXT)")
        db.executemany("INSERT INTO labels VALUES (?,?,'validation')", ((identity, int(i in (0, 12, 18, 30))) for i, identity in enumerate(identities)))
        db.execute("INSERT INTO labels VALUES ('forbidden-final-row',1,'test')")
    cohort = data / "build-report.json"
    write_json(cohort, {"artifact_sha256": {"dataset/evaluation-labels.sqlite": sha256_file(labels)}, "partitions": {"validation": {"rows": 36, "fraud_rows": 4}}})
    manifest = data / "manifest.json"
    write_json(manifest, {"cohort_report": str(cohort), "cohort_report_sha256": sha256_file(cohort), "features_sha256": sha256_file(features), "labels_read": False})
    neural = tmp_path / "neural"; neural.mkdir()
    scores = np.linspace(.9, .1, 36, dtype=np.float32)
    for seed in (42, 43, 44):
        directory = neural / f"confirmation-seed-{seed}"; directory.mkdir()
        np.save(directory / "development-scores.npy", scores)
    np.save(neural / "ensemble-development-scores.npy", np.mean([scores, scores, scores], axis=0))
    write_json(neural / "data.json", {"manifest": str(manifest), "manifest_sha256": sha256_file(manifest), "features_sha256": sha256_file(features), "labels_sha256": sha256_file(labels)})
    write_json(neural / "selected-recipe.json", {"seeds": [42, 43, 44]})
    write_json(neural / "protocol.json", {"policy": "self_authored_fixture"})
    report = {"status": "neural_v2_development_complete", "final_test_read": False, "selected": {"seeds": [42, 43, 44]},
              "development_rows": 36, "development_positives": 4, "ensemble": {"daily_review": {"true_positives": 2}},
              "artifact_sha256": {str(p.relative_to(neural)).replace("\\", "/"): sha256_file(p) for p in neural.rglob("*") if p.is_file()}}
    write_json(neural / "report.json", report)
    reviews = tmp_path / "actual-reviews.sqlite"
    package = {"rule": {"version": 1, "name": "fixture", "all": [{"field": "merchant_seen_30d", "op": "eq", "value": 0}]}}
    state = {"candidate_id": "case-467716-rule", "version": 2, "status": "pending_review", "deployed": False,
             "package": package, "package_hash": digest(package)}
    with sqlite3.connect(reviews) as db:
        db.execute("CREATE TABLE candidates(id TEXT PRIMARY KEY,state_json TEXT)")
        db.execute("INSERT INTO candidates VALUES (?,?)", (state["candidate_id"], json.dumps(state)))
    return Path(__file__).resolve().parents[1], tmp_path / "experiment", neural, reviews


@pytest.fixture
def frozen(source):
    root, directory, neural, reviews = source
    freeze(root, directory, neural_run=neural, reviews_database=reviews)
    return source


def test_plan_does_not_open_outcomes_and_persists_neural_names(frozen, monkeypatch):
    root, directory, _, reviews = frozen
    original_hash = sha256_file(reviews)
    connect = sqlite3.connect
    def guarded(path, *args, **kwargs):
        if "evaluation-labels.sqlite" in str(path): raise AssertionError("Outcome database opened while planning")
        return connect(path, *args, **kwargs)
    monkeypatch.setattr(sqlite3, "connect", guarded)
    proof = materialize(root, directory)
    assert proof["labels_read"] is False and proof["baseline_kind"] == "neural_v2_ensemble"
    with connect(directory / "ranking-inputs.sqlite") as db:
        columns = [r[1] for r in db.execute("PRAGMA table_info(population)")]
        assert "neural_score" in columns and "hgb_score" not in columns
        assert db.execute("SELECT COUNT(*) FROM population").fetchone()[0] == 36
    assert sha256_file(reviews) == original_hash


def test_full_development_research_and_independent_sql_leave_candidate_v2_unchanged(frozen):
    root, directory, _, reviews = frozen
    original_hash = sha256_file(reviews)
    materialize(root, directory)
    result = evaluate(root, directory)
    proof = verify(root, directory)
    assert result["metrics"]["10/neural"]["true_positives"] == 2
    assert result["metrics"]["10/rule_first"]["true_positives"] == 4
    assert result["metrics"]["10/rule_first"]["net_tp"] == 2
    assert result["metrics"]["10/rule_only"]["reviews"] == 12
    assert result["metrics"]["10/rule_only"]["unused_capacity"] == 8
    assert proof["source_rows_checked"] == 36 and proof["ranking_lists_checked"] == 12 and proof["monthly_checks"] == 4
    assert not result["human_approved"] and not result["deployed"] and not result["final_test_evaluated"]
    assert result["research_review_status"] == "pending_review_unregistered_material"
    assert sha256_file(reviews) == original_hash
    package = json.loads((directory / "research-package.json").read_text(encoding="utf-8"))
    assert package["candidate_reference"]["version"] == 2 and not package["actual_review_database_modified"]
    assert "forbidden-final-row" not in (directory / "research-package.json").read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="already"): evaluate(root, directory)
    with pytest.raises(ValueError, match="already"): materialize(root, directory)


def test_adapter_rejects_outcome_labels_and_is_not_an_hgb_claim():
    from app.risk.capacity_policy import tie_key
    rows = [{"transaction_id": "example", "day": "2018-01-01", "amount_cents": 100, "rule_match": True, "neural_score": .5, "tie": tie_key("example")}]
    assert allocate_neural_day(rows, 5, "neural")[0]["transaction_id"] == "example"
    with pytest.raises(ValueError, match="outcomes_forbidden"):
        allocate_neural_day([rows[0] | {"is_fraud": True}], 5, "neural")


def test_changed_seed_average_is_not_accepted_as_neural_ensemble(frozen):
    root, directory, neural, _ = frozen
    altered = np.load(neural / "ensemble-development-scores.npy"); altered[0] += .001
    np.save(neural / "ensemble-development-scores.npy", altered)
    with pytest.raises(ValueError, match="input_changed"): materialize(root, directory)


def test_actual_review_database_changes_block_new_research(frozen):
    root, directory, _, reviews = frozen
    with sqlite3.connect(reviews) as db: db.execute("UPDATE candidates SET state_json='{}'")
    with pytest.raises(ValueError, match="actual_review_database_changed"): materialize(root, directory)


@pytest.mark.parametrize("artifact", ["ranking-inputs.sqlite", "planned-reviews.sqlite"])
def test_changed_plan_cannot_open_development_outcomes(frozen, artifact):
    root, directory, _, _ = frozen
    materialize(root, directory)
    with (directory / artifact).open("ab") as stream: stream.write(b"changed")
    with pytest.raises(ValueError, match="lists_changed"): evaluate(root, directory)
    assert not (directory / "labels-opened.json").exists()


def test_independent_sql_rejects_resealed_rule_and_review_corruption(frozen):
    root, directory, _, _ = frozen
    materialize(root, directory); evaluate(root, directory)
    with sqlite3.connect(directory / "planned-reviews.sqlite") as db:
        db.execute("UPDATE reviews SET rank=99 WHERE budget=10 AND policy='neural' AND transaction_id='tx00'")
    manifest = json.loads((directory / "plan-manifest.json").read_text(encoding="utf-8"))
    manifest["plans_sha256"] = sha256_file(directory / "planned-reviews.sqlite")
    write_json(directory / "plan-manifest.json", manifest)
    report = json.loads((directory / "report.json").read_text(encoding="utf-8"))
    report["plan_manifest_sha256"] = sha256_file(directory / "plan-manifest.json")
    marker = json.loads((directory / "labels-opened.json").read_text(encoding="utf-8"))
    marker["plan_manifest_sha256"] = report["plan_manifest_sha256"]
    write_json(directory / "labels-opened.json", marker)
    report["labels_opened_sha256"] = sha256_file(directory / "labels-opened.json")
    write_json(directory / "report.json", report)
    with pytest.raises(ValueError, match="ranking_differs"): verify(root, directory)


def test_damaged_metrics_cannot_be_published_as_verified(frozen):
    root, directory, _, _ = frozen
    materialize(root, directory); evaluate(root, directory)
    report = json.loads((directory / "report.json").read_text(encoding="utf-8"))
    report["metrics"]["10/neural"]["true_positives"] += 1
    write_json(directory / "report.json", report)
    with pytest.raises(ValueError, match="paired_metrics_differ"): verify(root, directory)
