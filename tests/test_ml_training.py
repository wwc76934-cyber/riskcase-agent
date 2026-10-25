import json
from pathlib import Path
import sqlite3

import pytest

np = pytest.importorskip("numpy")
pd = pytest.importorskip("pandas")
pytest.importorskip("sklearn")
import joblib
from threadpoolctl import threadpool_limits

from app.risk.ml_data import load_split
from app.risk.ml_models import CONTINUOUS, MODEL_COLUMNS, make_model, signed_log1p
from app.risk.ml_evaluation import evaluate_scores, error_groups
from app.risk.rule_baseline import DailyReviewer


def example_frame(n=100):
    rng = np.random.default_rng(42)
    X = pd.DataFrame({name: rng.uniform(0, 10, n) for name in MODEL_COLUMNS if name not in ("channel", "mcc")})
    X["hour"] = np.arange(n) % 24
    X["weekday"] = np.arange(n) % 7
    X["merchant_seen_30d"] = np.arange(n) % 2
    X["has_history_30d"] = 1
    X["channel"] = np.where(np.arange(n) % 2 == 0, "Online", "Chip")
    X["mcc"] = np.where(np.arange(n) % 3 == 0, "1234", "5678")
    X.loc[0, "seconds_since_previous_in_snapshot"] = np.nan
    y = (np.arange(n) % 5 == 0).astype(np.int8)
    meta = pd.DataFrame({"transaction_id": [f"t-{n}" for n in range(n)], "user_id": "A",
                         "event_time": "2018-01-01T12:00:00", "split": "validation"})
    return X, y, meta


def policy():
    value = json.loads((Path(__file__).parents[1] / "config/ml-experiment-v1.json").read_text())
    value["hist_gradient_boosting"].update(max_iter=3, min_samples_leaf=2)
    return value


def test_signed_log_retains_sign_missing_and_order():
    actual = signed_log1p(np.array([-100, 0, 100, np.nan]))
    assert actual[0] == -actual[2]
    assert actual[1] == 0
    assert np.isnan(actual[3])


@pytest.mark.parametrize("name", ["logistic_regression", "hist_gradient_boosting"])
def test_pipeline_unknown_categories_missing_values_and_reload(name, tmp_path):
    X, y, _ = example_frame()
    with threadpool_limits(limits=1):
        model = make_model(name, policy()).fit(X, y)
        query = X.iloc[:3].copy()
        query["channel"] = "UNSEEN"
        query["mcc"] = "NEW"
        scores = model.predict_proba(query)[:, 1]
        assert np.isfinite(scores).all()
        assert ((scores >= 0) & (scores <= 1)).all()
        target = tmp_path / "own-model.joblib"
        joblib.dump(model, target)
        assert np.allclose(joblib.load(target).predict_proba(query)[:, 1], scores)
        encoder = model.named_steps["preprocess"].named_transformers_["categorical"]
        assert "UNSEEN" not in encoder.categories_[0]


def test_preprocessing_parameters_do_not_change_when_predicting_validation():
    X, y, _ = example_frame()
    with threadpool_limits(limits=1):
        model = make_model("logistic_regression", policy()).fit(X, y)
        pipe = model.named_steps["preprocess"].named_transformers_["continuous"]
        median, means = pipe.named_steps["impute"].statistics_.copy(), pipe.named_steps["scale"].mean_.copy()
        query = X.iloc[:2].copy()
        query.loc[:, list(CONTINUOUS)] = 1e12
        model.predict_proba(query)
        assert np.array_equal(median, pipe.named_steps["impute"].statistics_)
        assert np.array_equal(means, pipe.named_steps["scale"].mean_)


def test_classifier_configuration_avoids_internal_random_validation():
    model = make_model("hist_gradient_boosting", policy()).named_steps["model"]
    assert model.early_stopping is False
    assert model.class_weight == "balanced"
    assert not {"transaction_id", "is_fraud", "split", "user_id", "history_30d_complete"} & set(MODEL_COLUMNS)


def test_data_loader_rejects_test_before_database_access():
    class ForbiddenConnection:
        def cursor(self):
            raise AssertionError("Must not query this database")
    with pytest.raises(ValueError, match="not permitted"):
        load_split(ForbiddenConnection(), "test", expected_rows=1, expected_positives=1)


def test_load_split_isolates_labels_and_checks_join(tmp_path):
    X, _, _ = example_frame(2)
    X = X.astype({"channel": str, "mcc": str})
    X.insert(0, "transaction_id", ["a", "b"])
    X["user_id"], X["event_time"], X["split"], X["history_30d_complete"] = "A", "2018-01-01T00:00:00", "validation", 1
    with sqlite3.connect(":memory:") as db:
        X.to_sql("features", db, index=False)
        db.execute("ATTACH DATABASE ':memory:' AS evaluation")
        db.execute("CREATE TABLE evaluation.labels (transaction_id TEXT,split TEXT,is_fraud INTEGER)")
        db.executemany("INSERT INTO evaluation.labels VALUES (?,?,?)", [("a", "validation", 0), ("b", "validation", 1)])
        clean, y, meta = load_split(db, "validation", expected_rows=2, expected_positives=1)
        assert list(clean.columns) == list(MODEL_COLUMNS)
        assert "is_fraud" not in clean and "split" not in clean
        assert y.tolist() == [0, 1]
        assert meta["transaction_id"].tolist() == ["a", "b"]
        db.execute("DELETE FROM evaluation.labels WHERE transaction_id='b'")
        with pytest.raises(ValueError, match="join"):
            load_split(db, "validation", expected_rows=2, expected_positives=1)


def test_rank_metrics_float_scores_and_subgroups_share_global_budget():
    X, y, meta = example_frame(20)
    metrics, queue = evaluate_scores(meta, y, np.where(y == 1, 0.9, 0.1), daily_budget=2)
    assert metrics["average_precision"] == 1
    assert metrics["daily_review"]["true_positives"] == 2
    assert metrics["daily_review"]["reviewed_without_threshold_hit"] is None
    assert all("is_fraud" not in row for row in queue)
    groups = error_groups(meta, X, y, queue, train_users={"A"})
    assert sum(g["reviewed_or_alerted"] for g in groups["channel"].values()) == 2


@pytest.mark.parametrize("score", [np.nan, np.inf, True])
def test_invalid_scores_rejected(score):
    reviewer = DailyReviewer()
    with pytest.raises(ValueError):
        reviewer.add(transaction_id="a", event_time="2018-01-01", score=score, is_fraud=True,
                     reasons=[], threshold_alert=None)


def test_final_test_evaluation_and_nonbinary_outcomes_fail():
    _, y, meta = example_frame(10)
    with pytest.raises(ValueError, match="final test is forbidden"):
        evaluate_scores(meta.assign(split="test"), y, np.zeros(10))
    with pytest.raises(ValueError):
        evaluate_scores(meta, np.full(10, 2), np.zeros(10))
