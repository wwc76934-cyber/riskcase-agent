"""Meaningful neural-loop checks, independent of the large transaction snapshot."""

from pathlib import Path
import json

import pytest

np = pytest.importorskip("numpy")
torch = pytest.importorskip("torch")
from scipy import sparse

from app.risk.neural_model import RiskMLP, EarlyStop, dense_float32, make_loader, make_loss, predict_batches, seed_training, train_one_epoch


def fixture():
    X = np.random.default_rng(3).normal(size=(64, 2)).astype(np.float32)
    return X, (X[:, 0] > 0).astype(np.int8)


def test_tiny_problem_learns_and_updates_parameters():
    seed_training(42, 1)
    X, y = fixture()
    model = RiskMLP(2, (8, 4), 0)
    criterion, _ = make_loss(y)
    before = {name: value.clone() for name, value in model.state_dict().items()}
    _, initial_loss = predict_batches(model, make_loader(X, y), criterion)
    optimizer = torch.optim.AdamW(model.parameters(), lr=.03)
    for _ in range(45):
        stats = train_one_epoch(model, make_loader(X, y), optimizer, criterion)
    scores, loss = predict_batches(model, make_loader(X, y), criterion)
    assert loss < initial_loss * .35
    assert ((scores > .5) == y).mean() > .95
    assert stats["maximum_gradient_norm"] > 0
    assert any(not torch.equal(value, before[name]) for name, value in model.state_dict().items())


def test_evaluation_disables_dropout_gradients_and_parameter_updates():
    seed_training(42, 1)
    X, y = fixture()
    model = RiskMLP(2, (8, 4), .5)
    before = {name: value.clone() for name, value in model.state_dict().items()}
    first, _ = predict_batches(model, make_loader(X, y))
    second, _ = predict_batches(model, make_loader(X, y))
    assert np.array_equal(first, second)
    assert model.training is False
    assert all(parameter.grad is None for parameter in model.parameters())
    assert all(torch.equal(value, before[name]) for name, value in model.state_dict().items())


def test_weights_only_checkpoint_reproduces_own_predictions(tmp_path):
    seed_training(42, 1)
    X, _ = fixture()
    model = RiskMLP(2)
    first, _ = predict_batches(model, make_loader(X))
    path = tmp_path / "own-state.pt"
    torch.save(model.state_dict(), path)
    loaded = RiskMLP(2)
    loaded.load_state_dict(torch.load(path, map_location="cpu", weights_only=True))
    second, _ = predict_batches(loaded, make_loader(X))
    assert np.array_equal(first, second)


def test_stopping_keeps_earliest_tie_and_copies_parameters():
    model = RiskMLP(2, (3,), 0)
    stop = EarlyStop(patience=2, minimum_improvement=.001)
    assert stop.update(.2, 1, model) == (True, False)
    saved = {key: value.clone() for key, value in stop.best_state.items()}
    with torch.no_grad():
        next(model.parameters()).add_(3)
    assert stop.update(.2001, 2, model) == (False, False)
    assert stop.update(.2, 3, model) == (False, True)
    assert stop.best_epoch == 1
    assert all(torch.equal(value, saved[key]) for key, value in stop.best_state.items())


def test_seed_repeats_initialization_shuffle_and_cpu_training():
    X, y = fixture()
    scores = []
    for _ in range(2):
        seed_training(42, 1)
        model = RiskMLP(2, (8, 4), .1)
        criterion, _ = make_loss(y)
        optimizer = torch.optim.AdamW(model.parameters(), lr=.01)
        train_one_epoch(model, make_loader(X, y, shuffle=True, batch_size=15), optimizer, criterion)
        scores.append(predict_batches(model, make_loader(X))[0])
    assert np.array_equal(*scores)


def test_partial_batch_count_and_class_weight():
    seed_training(42, 1)
    X, y = fixture()
    criterion, ratio = make_loss(y)
    assert ratio == (y == 0).sum() / (y == 1).sum()
    model = RiskMLP(2, (4,), 0)
    stats = train_one_epoch(model, make_loader(X, y, batch_size=15), torch.optim.AdamW(model.parameters()), criterion)
    assert stats["rows"] == 64 and stats["batches"] == 5


def test_nonfinite_gradient_aborts_before_optimizer_step():
    seed_training(42, 1)
    X, y = fixture()
    model = RiskMLP(2, (4,), 0)
    original = {key: value.clone() for key, value in model.state_dict().items()}
    parameter = next(model.parameters())
    hook = parameter.register_hook(lambda gradient: torch.full_like(gradient, float("nan")))
    criterion, _ = make_loss(y)
    with pytest.raises(ValueError, match="gradient"):
        train_one_epoch(model, make_loader(X, y), torch.optim.AdamW(model.parameters()), criterion)
    hook.remove()
    assert all(torch.equal(value, original[key]) for key, value in model.state_dict().items())


def test_dense_conversion_checks_memory_and_finite_values():
    assert dense_float32(sparse.csr_matrix([[0, 1], [2, 0]])).dtype == np.float32
    with pytest.raises(ValueError, match="limit"):
        dense_float32(sparse.csr_matrix((100, 100)), max_bytes=4)
    with pytest.raises(ValueError, match="finite"):
        dense_float32(np.array([[np.nan]]))


@pytest.mark.parametrize("y", [[0, 0], [1, 1], [0, 2], [0, np.nan]])
def test_invalid_training_targets_fail(y):
    with pytest.raises(ValueError):
        make_loss(y)


def test_loader_rejects_bad_targets_or_nonfinite_inputs():
    X, _ = fixture()
    with pytest.raises(ValueError):
        make_loader(X, [0, 1])
    X[0, 0] = np.inf
    with pytest.raises(ValueError):
        make_loader(X)


def test_frozen_protocol_keeps_2018_out_of_epoch_selection():
    policy = json.loads((Path(__file__).resolve().parents[1] / "config/mlp-experiment-v1.json").read_text())
    assert policy["selection_validation"]["end"] == "2018-01-01"
    assert policy["refit"]["end"] == "2018-01-01"
    assert policy["final_test_2019"] == "forbidden"
