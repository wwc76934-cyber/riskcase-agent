import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import torch

from app.risk.ml_models import MODEL_COLUMNS
from app.risk.semantic_detection.data import label_mask, validate_frame
from app.risk.semantic_detection.evaluation import Ranking
from app.risk.semantic_detection.losses import class_balanced_weights, consistency_loss, supervised_loss
from app.risk.semantic_detection.model import Detector, DetectorConfig, Predictor, Preprocessor, load_model
from app.risk.semantic_detection.semantics import category_key, category_text, last_token_pool, load_cache
from app.risk.semantic_detection.temporal import historical_neighbors


def frame(n=16):
    x = pd.DataFrame({name: np.arange(n, dtype=float) for name in MODEL_COLUMNS if name not in ('mcc', 'channel')})
    x['mcc'] = ['5411', '5812'] * (n // 2)
    x['channel'] = ['Online Transaction', 'Chip Transaction'] * (n // 2)
    return x[list(MODEL_COLUMNS)]


def metadata():
    return pd.DataFrame({'transaction_id': ['a', 'b', 'c', 'd'], 'user_id': ['u', 'u', 'u', 'v'],
                         'event_time': ['2018-01-01T09:00', '2018-01-01T10:00', '2018-01-01T10:00', '2018-01-01T09:30'],
                         'split': ['validation'] * 4})


@pytest.mark.parametrize('value', [None, '', '5411.5', '5411;DROP', '商户123', np.nan])
def test_invalid_mcc_unknown(value):
    assert category_key(value, 'Chip Transaction')[0] == 'unknown'


def test_semantic_text_only_verified():
    glossary = {'entries': {'5411': {'title': 'Grocery stores'}}}
    assert category_text(('5411', 'Chip Transaction'), glossary) == 'Merchant category: Grocery stores. Transaction channel: chip card transaction.'
    assert category_text(('9999', 'not an allowed channel'), glossary) == 'Merchant category: unknown. Transaction channel: unknown.'


def test_padding_pool():
    x = torch.arange(12.).reshape(2, 3, 2)
    mask = torch.tensor([[0, 1, 1], [1, 1, 0]])
    assert torch.equal(last_token_pool(x, mask), torch.stack([x[0, 2], x[1, 1]]))
    with pytest.raises(ValueError):
        last_token_pool(x, torch.zeros_like(mask))


@pytest.mark.parametrize('extra', ['is_fraud', 'transaction_id', 'user_id', 'future_label', 'agent_memory'])
def test_model_feature_allowlist(extra):
    x = frame(); x[extra] = 0
    with pytest.raises(ValueError):
        validate_frame(x)


@pytest.mark.parametrize('fraction', [.05, .1, .2, 1.])
def test_label_masks_frozen_and_hidden(fraction):
    ids = [str(i) for i in range(80)]; y = np.array([0] * 76 + [1] * 4)
    mask, exposed = label_mask(ids, y, fraction)
    assert (exposed[~mask] == -1).all()
    assert set(exposed[mask]) == {0, 1}
    assert np.array_equal(mask, label_mask(ids, y, fraction)[0])
    if fraction == 1:
        assert mask.all()


def test_history_excludes_same_time_future_and_other_user():
    meta = metadata()
    neighbors, ages = historical_neighbors(meta)
    assert neighbors[0].tolist() == [-1] * 6
    assert neighbors[1, 0] == 0 and neighbors[2, 0] == 0
    assert 1 not in neighbors[2] and 2 not in neighbors[1]
    assert (neighbors[3] == -1).all()
    assert ages[1, 0] == 3600
    with pytest.raises(ValueError):
        historical_neighbors(meta, training=True)


def test_user_card_joint_key():
    meta = metadata(); meta['card_id'] = ['1', '2', '1', '1']
    neighbors, _ = historical_neighbors(meta, group='user_card')
    assert (neighbors[1] == -1).all()
    assert neighbors[2, 0] == 0
    assert (neighbors[3] == -1).all()


def test_future_append_cannot_change_existing_neighbors():
    meta = metadata()
    before, _ = historical_neighbors(meta)
    later = pd.DataFrame([{'transaction_id': 'later', 'user_id': 'u', 'event_time': '2018-12-01T00:00', 'split': 'validation'}])
    after, _ = historical_neighbors(pd.concat([meta, later], ignore_index=True))
    assert np.array_equal(before, after[:len(meta)])


def test_history_rejects_future_even_when_mislabeled_train_and_missing_card():
    meta = metadata(); meta['split'] = 'train'
    with pytest.raises(ValueError, match='2016-2017'):
        historical_neighbors(meta, training=True)
    meta['card_id'] = [None] * len(meta)
    with pytest.raises(ValueError, match='Known'):
        historical_neighbors(meta, group='user_card')


def test_losses_masks_and_gradients():
    first = torch.tensor([-.5, .5, 1.], requires_grad=True)
    second = torch.tensor([.2, .4, -1.], requires_grad=True)
    labels = torch.tensor([0., 1., -1.])
    loss = supervised_loss(first, labels) + consistency_loss(first, second, labels)
    loss.backward()
    assert torch.isfinite(first.grad).all() and first.grad[2] != 0
    assert second.grad[0] == 0 and second.grad[1] == 0 and second.grad[2] != 0
    assert consistency_loss(first, first, labels).item() == 0
    weights = class_balanced_weights(torch.tensor([0, 0, 0, 1]))
    assert weights[1] > weights[0] and abs(sum(weights) - 2) < 1e-8


@pytest.mark.parametrize('variant', ['B0', 'B1', 'B2', 'B3', 'B3_shuffled'])
def test_reload_and_frozen_vectors(variant, tmp_path):
    torch.set_num_threads(1); torch.manual_seed(42)
    x = frame(); keys = [('unknown', 'unknown'), ('5411', 'Online Transaction'), ('5812', 'Chip Transaction')]
    table = np.random.default_rng(42).normal(size=(3, 32)).astype(np.float32)
    prep = Preprocessor().fit(x, keys)
    model = Detector(DetectorConfig(variant=variant, width=8, blocks=1), table, prep.numeric.categorical_sizes)
    loss = model(*prep.transform(x)).square().mean(); loss.backward()
    if variant in {'B2', 'B3', 'B3_shuffled'}:
        assert not model.category.weight.requires_grad and model.category.weight.grad is None
    predictor = Predictor(model.config, prep, model, list(table.shape))
    before = predictor.predict(x); predictor.save(tmp_path / 'model')
    assert np.max(np.abs(load_model(tmp_path / 'model').predict(x) - before)) < 1e-7
    unknown = x.iloc[:1].copy(); unknown['mcc'] = '9999'
    assert prep.transform(unknown)[2].item() == 0


def test_unknown_vocabulary_and_train_only_preprocessing():
    x = frame(); keys = [('unknown', 'unknown'), ('5411', 'Online Transaction'), ('5812', 'Chip Transaction')]
    prep = Preprocessor().fit(x, keys)
    before = prep.numeric.median.copy()
    future = x.copy(); future['amount_cents'] = 1e9; future['channel'] = 'invented'
    tensors = prep.transform(future)
    assert np.array_equal(prep.numeric.median, before)
    assert torch.isfinite(tensors[0]).all() and (tensors[1][:, 1] == 0).all()


def test_ranking_ties_independent():
    from app.risk.ml_evaluation import evaluate_scores
    meta = metadata(); y = np.array([1, 0, 1, 0]); scores = np.ones(4) * .5
    ranking = Ranking(meta, y, budget=2)
    checked, queue = evaluate_scores(meta, y, scores, daily_budget=2)
    assert set(meta.transaction_id.iloc[ranking.selected(scores)]) == {r['transaction_id'] for r in queue}
    assert ranking.metrics(scores)['tp_daily10'] == checked['daily_review']['true_positives']
    assert ranking.compare(scores, scores)['net'] == 0
    meta['split'] = 'test'
    with pytest.raises(ValueError):
        Ranking(meta, y)


def test_tampered_cache_rejected(tmp_path):
    (tmp_path / 'manifest.json').write_text(json.dumps({'vectors_sha256': 'wrong'}))
    np.save(tmp_path / 'vectors.npy', np.ones((1, 32)))
    with pytest.raises(ValueError, match='changed'):
        load_cache(tmp_path)


def test_focal_gamma_zero_matches_bce_and_hidden_labels_no_gradient():
    z = torch.tensor([-50., 50., 4.], requires_grad=True)
    y = torch.tensor([0., 1., -1.])
    bce = supervised_loss(z, y, positive_weight=1.)
    focal = supervised_loss(z, y, kind='focal', gamma=0.)
    assert torch.allclose(bce, focal)
    bce.backward()
    assert z.grad[2].item() == 0
    cb = supervised_loss(z, y, kind='cb_focal', class_weights=[.1, 1.9])
    assert torch.isfinite(cb)


def test_short_training_learns_and_masked_training_reloads(tmp_path):
    from app.risk.semantic_detection.experiment import fit
    x = frame(64); x['amount_cents'] = np.tile([-1000., 1000.], 32)
    y = np.tile([0, 1], 32)
    keys = [('unknown', 'unknown'), ('5411', 'Online Transaction'), ('5812', 'Chip Transaction')]
    table = np.random.default_rng(2).normal(size=(3, 32)).astype(np.float32)
    predictor, result = fit(DetectorConfig(width=8, blocks=1, learning_rate=.01, positive_weight=1, batch_size=64),
                            x, y, keys, table, seed=42, epochs=30, output=tmp_path / 'learning')
    assert result['curves'][-1]['loss'] < result['curves'][0]['loss'] * .4
    predicted = predictor.predict(x)
    assert predicted[y == 1].mean() > predicted[y == 0].mean() + .5
    exposed = np.where(np.arange(64) % 4 < 2, y, -1)
    predictor, report = fit(DetectorConfig(width=8, blocks=1, batch_size=32), x, exposed, keys, table,
                            seed=42, epochs=2, output=tmp_path / 'masked', loss_kind='cb_focal', consistency_weight=.4)
    assert report['unlabeled'] == 32 and report['exposed_positive'] == 16
    assert np.max(np.abs(predictor.predict(x) - load_model(tmp_path / 'masked/model').predict(x))) < 1e-7


def test_graph_empty_history_keeps_self_and_has_finite_gradients():
    x = frame(); keys = [('unknown', 'unknown'), ('5411', 'Online Transaction'), ('5812', 'Chip Transaction')]
    prep = Preprocessor().fit(x, keys); tensors = prep.transform(x)
    model = Detector(DetectorConfig(width=8, blocks=1, history=True), np.ones((3, 32), dtype=np.float32), prep.numeric.categorical_sizes)
    n = len(x); k = 2
    history = (tensors[0][:, None].expand(-1, k, -1), tensors[1][:, None].expand(-1, k, -1),
               tensors[2][:, None].expand(-1, k), torch.zeros((n, k), dtype=torch.bool), torch.zeros((n, k)))
    model.eval()
    prediction = model(*tensors, history=history)
    expected = model.head(model.encode(*tensors)).squeeze(-1)
    assert torch.allclose(prediction, expected)
    prediction.square().mean().backward()
    assert all(p.grad is None or torch.isfinite(p.grad).all() for p in model.parameters())


def test_public_glossary_conflicts_never_assigned():
    path = Path(__file__).resolve().parents[2] / 'config/semantic-detection/mcc-glossary.json'
    glossary = json.loads(path.read_text(encoding='utf-8'))
    assert '5912' in glossary['conflicting_source_codes']
    assert '5912' not in glossary['entries'] and '5921' not in glossary['entries']
    assert 'unknown' in category_text(('5912', 'Chip Transaction'), glossary)


def test_research_artifact_path_traversal_rejected(tmp_path):
    from app.risk.semantic_detection.adapter import checked_run
    (tmp_path / 'report.json').write_text(json.dumps({'status': 'E1_complete', 'final_2019_read': False,
                                                   'artifact_sha256': {'../outside': '0' * 64}}))
    with pytest.raises(ValueError):
        checked_run(tmp_path)


@pytest.mark.parametrize('scores', [[.1, .2, .3, .4], [.5, .5, .5, .5], [.1, .2, .2, .4]])
def test_manual_ap_tie_groups(scores):
    from app.cli.semantic_detection_audit import manual_ap
    from sklearn.metrics import average_precision_score
    y = np.array([0, 1, 0, 1])
    assert abs(manual_ap(y, scores) - average_precision_score(y, scores)) < 1e-12


@pytest.mark.parametrize('variant', ['B1', 'B2', 'B3', 'B3_shuffled'])
def test_additive_contract_keeps_original_categories_even_when_semantics_unknown(variant):
    x = frame(); keys = [('unknown', 'unknown'), ('5411', 'Online Transaction'), ('5812', 'Chip Transaction')]
    prep = Preprocessor().fit(x, keys)
    model = Detector(DetectorConfig(variant=variant, width=8, blocks=1), np.ones((3, 32), dtype=np.float32), prep.numeric.categorical_sizes)
    assert model.input[0].in_features == 24 + 32 + 32
    numeric = torch.zeros((2, 24)); categories = torch.tensor([[1, 1], [2, 1]]); pairs = torch.zeros(2, dtype=torch.long)
    model.eval()
    logits = model(numeric, categories, pairs)
    assert not torch.allclose(logits[0], logits[1])
    logits.sum().backward()
    assert model.base_categories[0].weight.grad is not None


def test_archived_replacement_pilot_still_loadable(tmp_path):
    from app.risk.semantic_detection.model import ReplacementPilotDetector
    x = frame(); keys = [('unknown', 'unknown'), ('5411', 'Online Transaction'), ('5812', 'Chip Transaction')]
    prep = Preprocessor().fit(x, keys); config = DetectorConfig(variant='B2', width=8, blocks=1)
    model = ReplacementPilotDetector(config, np.ones((3, 32), dtype=np.float32), prep.numeric.categorical_sizes)
    predictor = Predictor(config, prep, model, [3, 32]); predictor.save(tmp_path / 'pilot')
    assert np.array_equal(predictor.predict(x), load_model(tmp_path / 'pilot').predict(x))


def test_history_sampler_zeroes_absent_slots_before_model():
    from app.risk.semantic_detection.history_experiment import gather_history
    tensors = (torch.full((2, 24), 99.), torch.ones((2, 2), dtype=torch.long), torch.ones(2, dtype=torch.long))
    neighbors = np.array([[-1, -1], [0, -1]])
    ages = np.array([[0., 0.], [60., 0.]], dtype=np.float32)
    values = gather_history(tensors, neighbors, ages, np.array([0, 1]))
    assert (values[0][0] == 0).all() and (values[1][0] == 0).all() and (values[2][0] == 0).all()
    assert (values[0][1, 0] == 99).all()
    ages[1, 0] = 0
    with pytest.raises(ValueError, match='Strictly past'):
        gather_history(tensors, neighbors, ages, np.array([1]))


@pytest.mark.parametrize('mode', ['mean', 'attention'])
def test_actual_history_inference_future_append_and_reload(mode, tmp_path):
    from app.risk.semantic_detection.history_experiment import predict_history
    x = frame(); keys = [('unknown', 'unknown'), ('5411', 'Online Transaction'), ('5812', 'Chip Transaction')]
    meta = pd.DataFrame({'transaction_id': [str(i) for i in range(16)], 'user_id': ['u'] * 16,
                         'event_time': pd.date_range('2016-01-01', periods=16, freq='h').astype(str), 'split': ['train'] * 16})
    prep = Preprocessor().fit(x, keys)
    config = DetectorConfig(width=8, blocks=1, history=True, history_mode=mode)
    model = Detector(config, np.ones((3, 32), dtype=np.float32), prep.numeric.categorical_sizes)
    predictor = Predictor(config, prep, model, [3, 32]); pos = np.array([0, 4, 15])
    before = predict_history(predictor, x, meta, pos)
    later = x.iloc[-1:].copy(); later['amount_cents'] = 1e12
    later_meta = pd.DataFrame([{'transaction_id': 'future', 'user_id': 'u', 'event_time': '2018-01-01', 'split': 'validation'}])
    after = predict_history(predictor, pd.concat([x, later], ignore_index=True), pd.concat([meta, later_meta], ignore_index=True), pos)
    assert np.max(np.abs(before - after)) < 1e-7
    predictor.save(tmp_path / mode)
    assert np.max(np.abs(before - predict_history(load_model(tmp_path / mode), x, meta, pos))) < 1e-7


def test_encoder_identity_ignores_downloader_metadata(tmp_path):
    from app.risk.semantic_detection.semantics import encoder_fingerprint, ENCODER_FILES
    for name in ENCODER_FILES:
        (tmp_path / name).write_bytes(name.encode())
    before = encoder_fingerprint(tmp_path)
    (tmp_path / 'revision.json').write_text('local formatting and download timestamps')
    (tmp_path / '.cache').mkdir(); (tmp_path / '.cache/arbitrary').write_text('changes')
    assert encoder_fingerprint(tmp_path) == before
    (tmp_path / 'tokenizer.json').write_text('changed actual tokenizer')
    assert encoder_fingerprint(tmp_path) != before


@pytest.mark.parametrize('field', ['keys', 'texts'])
def test_cache_category_binding_tampering_rejected(tmp_path, field):
    from app.risk.semantic_detection.semantics import cache_identity
    from app.risk.semantic_detection.common import digest
    np.save(tmp_path / 'vectors.npy', np.ones((2, 32), dtype=np.float32))
    manifest = {'version': 2, 'keys': [['unknown', 'unknown'], ['5411', 'Chip Transaction']],
                'texts': ['unknown', 'Grocery stores'], 'dimensions': 32, 'glossary_sha256': '1' * 64,
                'encoder_files': {'config.json': '2' * 64}, 'encoder': {'revision': '3' * 40},
                'pooling': 'last_nonpadding_token', 'max_length': 128,
                'vectors_sha256': digest(tmp_path / 'vectors.npy')}
    manifest['cache_key'] = cache_identity(manifest)
    (tmp_path / 'manifest.json').write_text(json.dumps(manifest))
    load_cache(tmp_path)
    if field == 'keys':
        manifest['keys'][1][0] = '5412'
    else:
        manifest['texts'][1] = 'Unverified semantic substitution'
    (tmp_path / 'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='identity'):
        load_cache(tmp_path)
