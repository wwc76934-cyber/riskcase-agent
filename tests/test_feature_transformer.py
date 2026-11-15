"""Behavioral checks for the feature-token attention challenger."""
import numpy as np
import pytest
import torch

from app.risk.neural_v2 import NeuralConfig, fit_v2, load_v2
from tests.test_neural_v2 import frame


def config(**kwargs):
    return NeuralConfig(architecture="feature_transformer", width=16, blocks=1,
        dropout=0., learning_rate=.01, positive_weight=1, batch_size=64,
        threads=1, engineered=False, **kwargs)


def test_attention_learns_and_reload_is_stable(tmp_path):
    X = frame()
    y = (X.amount_cents > 0).to_numpy().astype(np.int8)
    losses = []
    predictor = fit_v2(X, y, config(), epochs=20,
        on_epoch=lambda row, *_: losses.append(row['loss']))
    scores = predictor.predict(X)
    assert losses[-1] < losses[0] * .5
    assert ((scores >= .5) == y).mean() >= .95
    predictor.save(tmp_path / 'model')
    restored = load_v2(tmp_path / 'model')
    np.testing.assert_allclose(scores, restored.predict(X), atol=1e-6)
    unknown = X.iloc[:3].assign(mcc='not-seen', amount_cents=np.nan)
    assert np.isfinite(restored.predict(unknown)).all()
    assert np.array_equal(scores, predictor.predict(X.assign(is_fraud=1, user_id='secret')))


def test_attention_gradients_reach_feature_tokens_and_projection():
    X = frame(); y = (X.amount_cents > 0).to_numpy().astype(np.int8)
    predictor = fit_v2(X, y, config(), epochs=1)
    model = predictor.model
    assert model.token_weight.grad.abs().sum() > 0
    assert model.encoder.layers[0].self_attn.in_proj_weight.grad.abs().sum() > 0
    assert torch.isfinite(model.token_weight.grad).all()


@pytest.mark.parametrize('settings', [{'width':18}, {'attention_heads':0}, {'attention_heads':9}])
def test_bad_attention_dimensions_fail_early(settings):
    with pytest.raises(ValueError):
        NeuralConfig(architecture='feature_transformer', **settings)
