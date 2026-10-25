"""Feature binding and interpretable sensitivity, not causal explanations or accuracy claims."""

from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import sqlite3

import pytest

from app.risk.case_snapshot import COLUMNS, export_case
from app.risk.detector_explanation import FrozenNeuralEnsemble, _json_file, explain_case, load_feature_input, models_from_neural_run, render_explanation, validate_input
from app.risk.features import FeatureTransaction, iter_history_features
from app.risk.history import sha256_file
from app.risk.ml_models import MODEL_COLUMNS


@pytest.fixture
def example(tmp_path):
    source = tmp_path / "source.sqlite"
    records = [
        ("older", 0, "u", "c", "2018-01-02T11:59:59", 900000, "Z", "Online", "5411", "validation"),
        ("boundary30", 1, "u", "c", "2018-01-02T12:00:00", 100, "A", "Online", "5411", "validation"),
        ("boundary24", 2, "u", "c", "2018-01-31T12:00:00", 200, "A", "Online", "5411", "validation"),
        ("boundary1", 3, "u", "c", "2018-02-01T11:00:00", 300, "B", "Online", "5411", "validation"),
        ("recent", 4, "u", "c", "2018-02-01T11:30:00", -400, "C", "Online", "5411", "validation"),
        ("target", 5, "u", "c", "2018-02-01T12:00:00", 10000, "Z", "Online", "5411", "validation"),
        ("simultaneous", 6, "u", "c", "2018-02-01T12:00:00", 9000000, "Z", "Online", "5411", "validation"),
        ("future", 7, "u", "c", "2018-02-01T13:00:00", 9999999, "Z", "Online", "5411", "validation"),
        ("other", 8, "other", "c", "2018-02-01T11:59:59", 9999999, "Z", "Online", "5411", "validation"),
    ]
    with sqlite3.connect(source) as db:
        db.execute("CREATE TABLE transactions(transaction_id TEXT,source_row INTEGER,user_id TEXT,card_id TEXT,event_time TEXT,amount_cents INTEGER,merchant_id TEXT,channel TEXT,mcc TEXT,split TEXT)")
        db.executemany("INSERT INTO transactions VALUES (?,?,?,?,?,?,?,?,?,?)", records)
    report = tmp_path / "source-report.json"
    report.write_text(json.dumps({"artifact_sha256": {"dataset/facts.sqlite": sha256_file(source)}}), encoding="utf-8")
    manifest = export_case(tmp_path / "case", source_database=source, expected_source_sha256=sha256_file(source),
                           transaction_id="target", alert={"transaction_id": "target", "score": .7},
                           coverage_start="2018-01-01T00:00:00", coverage_end="2019-01-01T00:00:00", full_source_scanned=True,
                           source_report_sha256=sha256_file(report))
    transactions = [FeatureTransaction(r[0], r[2], datetime.fromisoformat(r[4]), r[5], r[6], r[7], r[8], r[9]) for r in sorted(records, key=lambda r: (r[2], r[4], r[1]))]
    rows = list(iter_history_features(transactions, coverage_start=datetime(2018, 1, 1)))
    target = next(row for row in rows if row["transaction_id"] == "target")
    bound = {key: target[key] for key in ("transaction_id", "user_id", "event_time")} | {"features": {c: target[c] for c in MODEL_COLUMNS}}
    return manifest, bound, rows


class AnalyticalScorer:
    def __init__(self): self.calls = []
    def score_many(self, rows):
        self.calls.append(deepcopy(rows))
        return [[.1 + row["amount_cents"] / 1000000 + row["count_1h"] / 100 + (1 - row["merchant_seen_30d"]) / 10,
                 .2 + row["amount_cents"] / 1000000 + row["count_1h"] / 100 + (1 - row["merchant_seen_30d"]) / 10] for row in rows]


def test_bound_features_match_independent_stream_with_all_boundaries(example):
    manifest, bound, _ = example
    tools, features, proof = validate_input(manifest, bound)
    assert proof["status"] == "all_model_columns_verified"
    assert proof["unverified_fields"] == []
    assert (features["count_1h"], features["count_24h"], features["count_30d"]) == (2, 3, 4)
    assert features["sum_signed_cents_24h"] == 100 and features["sum_signed_cents_30d"] == 200
    assert features["merchant_seen_30d"] == 0 and features["seconds_since_previous_in_snapshot"] == 1800
    assert {r["transaction_id"] for r in tools.prior} == {"boundary30", "boundary24", "boundary1", "recent"}


def test_three_one_input_changes_exact_scores_and_no_mutation(example):
    manifest, bound, _ = example
    before = deepcopy(bound); scorer = AnalyticalScorer()
    result = explain_case(manifest, bound, scorer)
    assert bound == before and len(scorer.calls) == 1 and len(scorer.calls[0]) == 4
    for change, row in zip(result["perturbations"], scorer.calls[0][1:]):
        differences = {c for c in MODEL_COLUMNS if row[c] != bound["features"][c]}
        assert differences == {change["changed_column"]}
        assert change["status"] == "scored" and not change["causal"]
    # Positive historical values are 100, 200, 300; old/future/other-user values never enter the median.
    assert result["perturbations"][0]["after"] == 200
    assert result["perturbations"][0]["delta"] == pytest.approx((200 - 10000) / 1000000)
    assert result["perturbations"][1]["delta"] == pytest.approx(-.02)
    assert result["perturbations"][2]["delta"] == pytest.approx(-.1)
    assert not result["labels_read"] and not result["causal"] and result["fraud_verdict"] is None
    assert result["source_alert_score"] == .7 and result["same_as_alert_model_verified"] is False
    assert "不是欺诈概率" in render_explanation(result)


def test_bound_inputs_agree_with_direct_source_sql(example):
    manifest, bound, _ = example
    from datetime import timedelta
    cutoff = datetime.fromisoformat(bound["event_time"])
    with sqlite3.connect(manifest.parent.parent / "source.sqlite") as db:
        for window, hours in (("1h",1), ("24h",24), ("7d",168), ("30d",720)):
            begin = (cutoff-timedelta(hours=hours)).isoformat()
            count, amount = db.execute("SELECT COUNT(*),COALESCE(SUM(amount_cents),0) FROM transactions WHERE user_id=? AND event_time>=? AND event_time<?", ("u", begin, cutoff.isoformat())).fetchone()
            assert count == bound["features"]["count_"+window]
            if window in ("24h", "30d"): assert amount == bound["features"]["sum_signed_cents_"+window]
        positives = [r[0] for r in db.execute("SELECT amount_cents FROM transactions WHERE user_id=? AND event_time>=? AND event_time<? AND amount_cents>0 ORDER BY amount_cents", ("u", (cutoff-timedelta(days=30)).isoformat(), cutoff.isoformat()))]
    result = explain_case(manifest, bound, AnalyticalScorer())
    assert result["perturbations"][0]["after"] == positives[(len(positives)-1)//2]


@pytest.mark.parametrize("field,value", [("transaction_id", "future"), ("user_id", "other"), ("event_time", "2018-02-01T13:00:00")])
def test_wrong_binding_rejected_before_scoring(example, field, value):
    manifest, bound, _ = example
    scorer = AnalyticalScorer()
    with pytest.raises(ValueError, match="binding"): explain_case(manifest, bound | {field: value}, scorer)
    assert not scorer.calls


@pytest.mark.parametrize("field,value", [
    ("amount_cents", 123), ("count_1h", 1), ("count_30d", 9), ("sum_signed_cents_30d", 999),
    ("merchant_seen_30d", 1), ("seconds_since_previous_in_snapshot", 1200), ("hour", 11), ("mcc", "other"),
    ("channel", "Chip"),
])
def test_frozen_input_must_match_reconstructable_facts(example, field, value):
    manifest, bound, _ = example
    changed = deepcopy(bound); changed["features"][field] = value
    with pytest.raises(ValueError): validate_input(manifest, changed)


@pytest.mark.parametrize("field,value", [("amount_cents", True), ("count_1h", -1), ("count_1h", float("nan")), ("count_1h", 2.0), ("merchant_seen_30d", 2), ("seconds_since_previous_in_snapshot", 0)])
def test_invalid_raw_values_rejected(example, field, value):
    manifest, bound, _ = example
    changed = deepcopy(bound); changed["features"][field] = value
    with pytest.raises(ValueError): validate_input(manifest, changed)


def test_labels_unknown_columns_and_projection_tamper_rejected(example):
    manifest, bound, _ = example
    with pytest.raises(ValueError, match="explicit_bound"): validate_input(manifest, bound | {"is_fraud": 1})
    changed = deepcopy(bound); changed["features"]["is_fraud"] = 1
    with pytest.raises(ValueError, match="exact_model"): validate_input(manifest, changed)
    with sqlite3.connect(manifest.parent / "facts.sqlite") as db: db.execute("UPDATE transactions SET amount_cents=0 WHERE transaction_id='target'")
    with pytest.raises(ValueError, match="projection"): validate_input(manifest, bound)


def test_unrelated_table_even_with_updated_projection_hash_rejected(example):
    manifest, bound, _ = example
    database = manifest.parent / "facts.sqlite"
    with sqlite3.connect(database) as db: db.execute("CREATE TABLE labels(transaction_id TEXT,is_fraud INTEGER)")
    body = json.loads(manifest.read_text()); body["projection_sha256"] = sha256_file(database); manifest.write_text(json.dumps(body))
    with pytest.raises(ValueError, match="tables"): validate_input(manifest, bound)


@pytest.mark.parametrize("column,value", [("event_time", "2018-02-01T12:00:00"), ("event_time", "2018-02-01T13:00:00"), ("user_id", "other")])
def test_illegal_case_row_rejected_even_if_hash_is_updated(example, column, value):
    manifest, bound, _ = example
    database = manifest.parent / "facts.sqlite"
    with sqlite3.connect(database) as db: db.execute(f"UPDATE transactions SET {column}=? WHERE transaction_id='recent'", (value,))
    body = json.loads(manifest.read_text()); body["projection_sha256"] = sha256_file(database); manifest.write_text(json.dumps(body))
    with pytest.raises(ValueError): validate_input(manifest, bound)


def test_partial_coverage_is_explicit_and_skips_unprovable_baselines(example):
    manifest, bound, _ = example
    body = json.loads(manifest.read_text()); body["full_source_scanned"] = False; manifest.write_text(json.dumps(body))
    scorer = AnalyticalScorer(); result = explain_case(manifest, bound, scorer)
    assert result["input_validation"]["unverified_fields"]
    assert all(row["status"] == "skipped" for row in result["perturbations"])
    assert len(scorer.calls[0]) == 1


def test_empty_30d_history_cannot_invent_previous_lifetime_event(example):
    manifest, bound, _ = example
    database = manifest.parent / "facts.sqlite"
    with sqlite3.connect(database) as db: db.execute("DELETE FROM transactions WHERE transaction_id!='target'")
    body = json.loads(manifest.read_text()); body.update(prior_rows=0, projection_sha256=sha256_file(database)); manifest.write_text(json.dumps(body))
    changed = deepcopy(bound)
    for field in ("count_1h", "count_24h", "count_7d", "count_30d", "sum_signed_cents_24h", "sum_signed_cents_30d", "merchant_seen_30d", "has_history_30d"): changed["features"][field] = 0
    changed["features"]["seconds_since_previous_in_snapshot"] = 40*86400
    _, features, proof = validate_input(manifest, changed)
    assert features["seconds_since_previous_in_snapshot"] == 40*86400
    assert proof["unverified_fields"] == ["seconds_since_previous_in_snapshot"] and proof["previous_time_gap"]
    result = explain_case(manifest, changed, AnalyticalScorer())
    assert all(r["status"] == "skipped" for r in result["perturbations"])


@pytest.mark.parametrize("bad", [[], [[float("nan")]], [[-1]], [[1.1]], [[True]], [[.1], [.1]], [[.1,.2,.3,.4]]])
def test_invalid_prediction_shape_or_score_cannot_publish(example, bad):
    manifest, bound, _ = example
    class Bad:
        def score_many(self, rows): return bad*len(rows) if len(bad) == 1 else bad
    with pytest.raises(ValueError): explain_case(manifest, bound, Bad())


def test_reported_model_count_must_match_actual_scores(example):
    manifest, bound, _ = example
    with pytest.raises(ValueError, match="model_metadata_member_count"):
        explain_case(manifest, bound, AnalyticalScorer(), model_metadata={"model_count": 3})


def test_load_one_frozen_feature_row_without_label_database(example, tmp_path):
    manifest, bound, rows = example
    parent = tmp_path / "frozen-features"; parent.mkdir()
    database = parent / "features.sqlite"
    columns = ("transaction_id", "user_id", "event_time", *MODEL_COLUMNS)
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE features(" + ",".join(c + (" TEXT" if c in ("transaction_id", "user_id", "event_time", "channel", "mcc") else " INTEGER") for c in columns) + ")")
        db.executemany("INSERT INTO features VALUES (" + ",".join("?" for _ in columns) + ")", [tuple(row[c] for c in columns) for row in rows])
    case = json.loads(manifest.read_text()); path = parent / "manifest.json"
    path.write_text(json.dumps({"status": "features_built", "labels_read": False, "facts_sha256": case["source_facts_sha256"],
                               "cohort_report_sha256": case["source_report_sha256"], "features_sha256": sha256_file(database)}))
    loaded, proof = load_feature_input(path, manifest)
    assert loaded == bound and not proof["labels_read"] and proof["query_scope"] == "one bound transaction/user/time"
    with sqlite3.connect(database) as db: db.execute("UPDATE features SET user_id='wrong' WHERE transaction_id='target'")
    with pytest.raises(ValueError, match="database_changed"): load_feature_input(path, manifest)


def test_untrusted_or_changed_model_not_loaded(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="trusted_local"): FrozenNeuralEnsemble([], trusted_local=False)
    directory = tmp_path / "model"; directory.mkdir()
    for name in ("model.pt", "preprocessor.joblib"): (directory/name).write_text("not a model")
    called = []
    monkeypatch.setattr("app.risk.neural_v2.load_v2", lambda *_: called.append(True))
    with pytest.raises(ValueError, match="changed"):
        FrozenNeuralEnsemble([{"directory": str(directory), "artifact_sha256": {"model.pt": "0"*64, "preprocessor.joblib": "0"*64}}], trusted_local=True)
    assert not called


def test_model_registry_reads_only_saved_expected_hashes(tmp_path):
    run = tmp_path / "run"; run.mkdir()
    expected = {f"confirmation-seed-{seed}/model/{name}": str(seed)*32 for seed in (42,43,44) for name in ("model.pt", "preprocessor.joblib")}
    (run / "report.json").write_text(json.dumps({"artifact_sha256": expected}))
    models = models_from_neural_run(run)
    assert len(models) == 3 and models[0]["artifact_sha256"]["model.pt"] == "42"*32
    with pytest.raises(KeyError): models_from_neural_run(run, seeds=(99,))


def test_real_small_neural_ensemble_reload_and_sensitivity(example, tmp_path):
    np = pytest.importorskip("numpy")
    pd = pytest.importorskip("pandas")
    pytest.importorskip("torch")
    from app.risk.neural_v2 import NeuralConfig, fit_v2
    manifest, bound, _ = example
    training = pd.DataFrame([bound["features"] | {"amount_cents": 100 + 200*i} for i in range(16)])
    labels = (np.arange(16) >= 8).astype(np.int8)
    run = tmp_path / "local-neural-run"; run.mkdir()
    hashes = {}
    for seed in (42,43,44):
        directory = run / f"confirmation-seed-{seed}"; directory.mkdir()
        model = fit_v2(training, labels, NeuralConfig(width=8, blocks=1, dropout=0., positive_weight=1., batch_size=16, threads=1), seed=seed, epochs=2)
        model.save(directory / "model")
        for name in ("model.pt", "preprocessor.joblib"):
            hashes[f"confirmation-seed-{seed}/model/{name}"] = sha256_file(directory / "model" / name)
    (run / "report.json").write_text(json.dumps({"artifact_sha256": hashes}))
    ensemble = FrozenNeuralEnsemble(models_from_neural_run(run), trusted_local=True)
    result = explain_case(manifest, bound, ensemble, model_metadata=ensemble.metadata)
    assert len(result["original"]["member_scores"]) == 3 and result["model"]["model_artifacts_verified"]
    assert len([r for r in result["perturbations"] if r["status"] == "scored"]) == 3
    independent = [float(p.predict(pd.DataFrame([bound["features"]]))[0]) for p in ensemble.predictors]
    assert result["original"]["score"] == pytest.approx(sum(independent)/3, abs=1e-6)
    for name, expected in hashes.items(): assert sha256_file(run/name) == expected


def test_json_duplicates_nonfinite_and_oversize_refused(tmp_path):
    path = tmp_path / "input.json"
    for text in ('{"x":1,"x":2}', '{"x":NaN}', 'x'*200001):
        path.write_text(text)
        with pytest.raises(ValueError): _json_file(path)
