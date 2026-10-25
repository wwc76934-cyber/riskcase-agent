"""Residual model learning, data isolation, gradient safety and ranking equivalence."""

from dataclasses import asdict

import pytest
np = pytest.importorskip("numpy")
torch = pytest.importorskip("torch")
pd = pytest.importorskip("pandas")

from app.cli.train_neural_v2 import DevelopmentMetric
from app.risk.ml_evaluation import evaluate_scores
from app.risk.ml_models import MODEL_COLUMNS
from app.risk.neural_v2 import NeuralConfig, NeuralPreprocessor, TabularRiskNetwork, fit_v2, load_v2, numeric_frame, predict_tensors, predict_v2
from app.risk.neural_model import seed_training


def frame(count=64):
    rng = np.random.default_rng(7)
    result = pd.DataFrame({c: np.ones(count) for c in MODEL_COLUMNS if c not in ("channel", "mcc")})
    result["amount_cents"] = np.linspace(-10000, 10000, count)
    result["hour"] = rng.integers(0, 24, count)
    result["weekday"] = rng.integers(0, 7, count)
    result["channel"] = "Chip"; result["mcc"] = "5411"
    return result


def small_config(**kwargs):
    return NeuralConfig(width=16, blocks=1, dropout=0., learning_rate=.02, positive_weight=1, batch_size=64, threads=1, **kwargs)


def test_tiny_problem_learns_not_just_executes():
    X = frame(); y = (X["amount_cents"] > 0).to_numpy().astype(np.int8)
    curves = []
    predictor = fit_v2(X, y, small_config(), epochs=12, on_epoch=lambda row, *_: curves.append(row))
    assert curves[-1]["loss"] < curves[0]["loss"] * .3
    assert ((predictor.predict(X) >= .5) == y).mean() >= .95
    assert all(row["rows"] == 64 and row["batches"] == 1 for row in curves)
    assert any(row["max_preclip_gradient_norm"] > 0 for row in curves)


def test_preprocessor_train_only_and_unknown_categories():
    training = frame(); training.loc[0, "seconds_since_previous_in_snapshot"] = np.nan
    prep = NeuralPreprocessor(small_config(include_channel=True)).fit(training)
    medians, scales = prep.median.copy(), prep.scale.copy()
    extreme = training.iloc[:2].copy(); extreme["amount_cents"] = 1e20; extreme["mcc"] = "UNSEEN"; extreme["channel"] = "UNSEEN"
    numeric, cats = prep.transform(extreme)
    assert np.isfinite(numeric.numpy()).all() and numeric.abs().max() <= 8
    assert cats.eq(0).all() and np.array_equal(medians, prep.median) and np.array_equal(scales, prep.scale)
    assert numeric.shape[1] == 2 * len(prep.columns)


def test_identifiers_labels_and_future_columns_do_not_enter_model():
    X = frame(); prep = NeuralPreprocessor(small_config()).fit(X)
    original = prep.transform(X)
    polluted = X.assign(transaction_id="secret", user_id="other", is_fraud=1, future_amount=999999, split="test")
    assert all(torch.equal(a, b) for a, b in zip(original, prep.transform(polluted)))
    assert not {"transaction_id", "user_id", "is_fraud", "future_amount", "split"} & set(prep.columns)
    assert "channel" not in prep.categories


def test_prediction_disables_dropout_and_has_no_updates():
    config = NeuralConfig(width=16, blocks=1, dropout=.7, threads=1)
    seed_training(42, 1)
    prep = NeuralPreprocessor(config).fit(frame()); tensors = prep.transform(frame())
    model = TabularRiskNetwork(tensors[0].shape[1], prep.categorical_sizes, config)
    before = {k: v.clone() for k, v in model.state_dict().items()}
    first = predict_tensors(model, tensors); second = predict_tensors(model, tensors)
    assert np.array_equal(first, second) and not model.training
    assert all(p.grad is None for p in model.parameters())
    assert all(torch.equal(v, before[k]) for k, v in model.state_dict().items())


def test_save_reload_reproduces_predictions_and_refuses_overwrite(tmp_path):
    X = frame(); y = (X.amount_cents > 0).to_numpy().astype(np.int8)
    trained = fit_v2(X, y, small_config(), epochs=3)
    path = tmp_path / "model"; trained.save(path)
    loaded = load_v2(path)
    assert asdict(loaded.config) == asdict(trained.config)
    assert np.max(np.abs(trained.predict(X) - predict_v2(path, X))) <= 1e-6
    with pytest.raises(FileExistsError): trained.save(path)


def test_seed_repeats_training():
    X = frame(); y = (X.amount_cents > 0).to_numpy().astype(np.int8)
    first = fit_v2(X, y, small_config(), seed=43, epochs=3).predict(X)
    second = fit_v2(X, y, small_config(), seed=43, epochs=3).predict(X)
    assert np.array_equal(first, second)


@pytest.mark.parametrize("settings", [{"architecture": "gnn"}, {"width": 0}, {"dropout": 1}, {"positive_weight": float("nan")}, {"threads": 8}, {"batch_size": 0}])
def test_invalid_training_settings_rejected(settings):
    with pytest.raises(ValueError): NeuralConfig(**settings)


def test_infinite_or_missing_inputs_rejected():
    X = frame(); X.loc[0, "amount_cents"] = np.inf
    with pytest.raises(ValueError): NeuralPreprocessor(small_config()).fit(X)
    with pytest.raises(ValueError): numeric_frame(frame().drop(columns="mcc"))
    with pytest.raises(ValueError): fit_v2(frame(), np.zeros(64), small_config(), epochs=1)


def test_fast_ranking_matches_independent_heap_and_ties():
    count = 100
    meta = pd.DataFrame({"transaction_id": [f"row-{i}" for i in range(count)], "user_id": "u", "event_time": [f"2018-01-{i % 4 + 1:02d}T12:00:00" for i in range(count)], "split": "validation"})
    y = (np.arange(count) % 7 == 0).astype(np.int8)
    for score in (np.ones(count), np.arange(count) % 3, np.random.default_rng(42).uniform(size=count)):
        direct = DevelopmentMetric(meta, y, budget=3).calculate(score)
        independent, _ = evaluate_scores(meta, y, score, daily_budget=3)
        assert direct["true_positives"] == independent["daily_review"]["true_positives"]
        assert direct["reviewed"] == independent["daily_review"]["reviewed_or_alerted"]
        assert direct["average_precision"] == independent["average_precision"]


def test_independent_average_precision_preserves_tie_groups():
    from scripts.verify_neural_v2 import independent_ap
    from sklearn.metrics import average_precision_score
    y = np.array([0, 1, 0, 1, 1, 0])
    for scores in (np.ones(6), np.array([1., .5, 1., .5, 1., .2]), np.arange(6)):
        assert abs(independent_ap(y, scores) - average_precision_score(y, scores)) < 1e-12


@pytest.mark.parametrize("engineered", [True, False])
def test_matched_tree_sees_same_information_and_ignores_channel(tmp_path, engineered):
    import joblib
    from threadpoolctl import threadpool_limits
    from app.risk.detector_v2_controls import HistoricalFeatureView, make_hgb_v2
    X = frame(128); y = (X.amount_cents > 0).to_numpy().astype(np.int8)
    view = HistoricalFeatureView(engineered=engineered).fit(X).transform(X)
    assert set(view) == set(numeric_frame(X, engineered)) | {"mcc"}
    assert "channel" not in view and "user_id" not in view
    model = make_hgb_v2(engineered=engineered)
    with threadpool_limits(limits=1):
        model.fit(X, y)
        first = model.predict_proba(X)[:, 1]
        changed = X.assign(channel="UNSEEN", user_id="other", is_fraud=1)
        assert np.array_equal(first, model.predict_proba(changed)[:, 1])
        changed["mcc"] = "UNSEEN"
        assert np.isfinite(model.predict_proba(changed)).all()
        joblib.dump(model, tmp_path / "model.joblib")
        assert np.array_equal(first, joblib.load(tmp_path / "model.joblib").predict_proba(X)[:, 1])
