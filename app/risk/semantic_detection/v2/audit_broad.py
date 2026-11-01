"""Independent broad-feature reconstruction and complete saved-model score replay."""
import json
import math
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import torch
from threadpoolctl import threadpool_limits

from app.risk.ml_evaluation import evaluate_scores
from app.risk.semantic_detection.common import digest, write_json
from app.risk.semantic_detection.evaluation import Ranking
from .audit_history import block_interval
from .audit_matched import manual_ap
from .expanded_controls import BroadStatsModel, predict
from .history_data import load_with_cards
from .matched import Config


def reconstruct(numeric, categories, neighbors, category_size):
    """Use per-position sums and row/category bincount, independently of trainer."""
    n, width = len(neighbors), numeric.shape[1]
    count = (neighbors >= 0).sum(axis=1)
    denominator = np.maximum(count, 1)[:, None]
    sums = np.zeros((n, width), dtype=np.float64)
    squares = np.zeros_like(sums)
    frequency = np.zeros((n, category_size), dtype=np.float64)
    for column in range(neighbors.shape[1]):
        valid = neighbors[:, column] >= 0
        indices = neighbors[valid, column]
        values = numeric[indices].astype(np.float64)
        sums[valid] += values
        squares[valid] += values ** 2
        keys = np.flatnonzero(valid) * category_size + categories[indices]
        frequency += np.bincount(keys, minlength=n*category_size).reshape(n, category_size)
    mean = sums / denominator
    std = np.sqrt(np.maximum(squares / denominator - mean ** 2, 0))
    latest = np.zeros_like(sums)
    valid = count > 0
    latest[valid] = numeric[neighbors[valid, 0]]
    frequency /= denominator
    return mean, std, latest, frequency, valid


def run(source_root, history_run, broad_run, history_audit, output, *, tree_run):
    history_run, broad_run, output = map(Path, (history_run, broad_run, output))
    output.mkdir(parents=True, exist_ok=False)
    report = json.loads((broad_run/'report.json').read_text(encoding='utf8'))
    audited = json.loads((Path(history_audit)/'report.json').read_text(encoding='utf8'))
    if report['status'] != 'posthoc_broad_statistic_controls_complete' or audited['status'] != 'Gate_B_independently_verified':
        raise ValueError('Both completed study and independent source/graph proof are required')
    if audited['report_sha256'] != digest(history_run/'report.json'):
        raise ValueError('Different primary-history study')
    loaded, provenance, _ = load_with_cards(source_root)
    (X, y, meta), (VX, vy, vmeta) = loaded
    context = pd.concat([X, VX], ignore_index=True)
    stored = json.loads((broad_run/'features.json').read_text(encoding='utf8'))
    raw = np.load(broad_run/'broad-raw.npy', mmap_mode='r')
    normalized = np.load(broad_run/'broad-normalized.npy', mmap_mode='r')
    for name in ('raw', 'normalized'):
        if digest(broad_run/f'broad-{name}.npy') != stored[name+'_sha256']:
            raise ValueError('Feature matrix changed')
    prep = joblib.load(broad_run/'neural-seed-42/preprocessor.joblib')
    primary_prep = joblib.load(history_run/'flat/seed-42/model/preprocessor.joblib')
    if not np.array_equal(prep.numeric.median, primary_prep.numeric.median) or not np.array_equal(prep.numeric.scale, primary_prep.numeric.scale) or prep.numeric.vocabularies != primary_prep.numeric.vocabularies:
        raise ValueError('Broad and registered point preprocessing differ')
    tensors = prep.transform(context)
    numeric, categories = tensors[0].numpy(), tensors[1][:, 0].numpy()
    neighbors = np.load(history_run/'inference-neighbors24.npy', mmap_mode='r')[:, :6]
    train_neighbors = np.load(history_run/'training-neighbors24.npy', mmap_mode='r')[:, :6]
    if not np.array_equal(neighbors[:len(X)], train_neighbors):
        raise ValueError('Inference-prefix graph differs from training-only graph')
    old = np.load(history_run/'inference-statistics.npy', mmap_mode='r')
    max_difference = 0.
    for start in range(0, len(context), 8192):
        end = min(start+8192, len(context))
        mean, std, latest, frequency, valid = reconstruct(numeric, categories, neighbors[start:end], stored['training_mcc_categories'])
        delta = np.where(valid[:, None], numeric[start:end]-mean, 0.)
        expected = np.concatenate([old[start:end], mean, std, latest, delta, frequency], axis=1).astype(np.float32)
        difference = float(np.max(np.abs(expected-raw[start:end])))
        max_difference = max(max_difference, difference)
        if difference > 2e-6:
            raise ValueError('Independent broad-feature reconstruction differs')
    medians, scales = [], []
    for column in range(raw.shape[1]):
        values = np.asarray(raw[:len(X), column], dtype=np.float64)
        z = np.sign(values)*np.log1p(np.abs(values))
        medians.append(float(np.median(z)))
        q25, q75 = np.percentile(z, [25, 75])
        scales.append(float(max(q75-q25, .25)))
    if medians != stored['training_only_median'] or scales != stored['training_only_scale']:
        raise ValueError('Broad statistics scaler includes nontraining data')
    for start in range(0, len(context), 8192):
        values = np.asarray(raw[start:start+8192], dtype=np.float64)
        expected = np.clip((np.sign(values)*np.log1p(np.abs(values))-medians)/scales, -8, 8).astype(np.float32)
        if not np.array_equal(expected, normalized[start:start+8192]):
            raise ValueError('Normalized broad features differ')
    ranking = Ranking(vmeta, vy)
    scores_by_name, replays = {}, {}
    for seed in (42, 43, 44):
        folder = broad_run/f'neural-seed-{seed}'
        state = torch.load(folder/'model.pt', weights_only=True, map_location='cpu')
        model_prep = joblib.load(folder/'preprocessor.joblib')
        model = BroadStatsModel(Config(**state['config']), model_prep, state['dimensions'])
        model.load_state_dict(state['state_dict'])
        row = report['results'][f'neural-seed-{seed}']
        if len(row['curves']) != 12 or sum(p.numel() for p in model.parameters()) != row['parameters']:
            raise ValueError('Broad-model budget or capacity differs')
        for epoch, curve in enumerate(row['curves']):
            expected_lr = .00005+(.001-.00005)*(1+math.cos(math.pi*epoch/12))/2
            if curve['rows'] != len(X) or curve['positives'] != int(y.sum()) or abs(curve['lr']-expected_lr) > 1e-12:
                raise ValueError('Broad-model training exposure differs')
        with threadpool_limits(limits=2):
            torch.set_num_threads(2)
            replay = predict(model, model_prep, VX, normalized[len(X):])
        saved = np.load(folder/'development-scores.npy', allow_pickle=False)
        difference = float(np.max(np.abs(saved-replay)))
        if difference > 1e-6:
            raise ValueError('Full broad neural score replay differs')
        scores_by_name[f'broad_seed{seed}'] = saved
        replays[str(seed)] = {'rows': len(saved), 'max_abs_diff': difference, 'model_sha256': digest(folder/'model.pt')}
    ensemble = np.mean([scores_by_name[f'broad_seed{s}'] for s in (42, 43, 44)], axis=0)
    if not np.array_equal(ensemble, np.load(broad_run/'neural-ensemble.npy')):
        raise ValueError('Broad seed ensemble differs')
    scores_by_name['broad_neural'] = ensemble
    current = [c for c in X if c not in ('mcc', 'channel')]
    tree_frame = pd.concat([VX[current].astype(np.float32).reset_index(drop=True), pd.DataFrame(np.asarray(raw[len(X):]), columns=stored['feature_names'])], axis=1)
    tree_frame['mcc'] = VX.mcc.astype(str).to_numpy()
    tree = joblib.load(broad_run/'tree.joblib')
    with threadpool_limits(limits=2):
        replay = tree.predict_proba(tree_frame)[:, 1]
    saved = np.load(broad_run/'tree-development-scores.npy')
    difference = float(np.max(np.abs(saved-replay)))
    if difference > 1e-12:
        raise ValueError('Full broad tree score replay differs')
    scores_by_name['broad_tree'] = saved
    replays['tree'] = {'rows': len(saved), 'max_abs_diff': difference, 'model_sha256': digest(broad_run/'tree.joblib')}
    narrow_frame = VX[current+['mcc']].copy()
    for column, name in enumerate(stored['feature_names'][:14]):
        narrow_frame[name] = old[len(X):, column]
    narrow_folder = Path(tree_run)
    with threadpool_limits(limits=2):
        narrow_replay = joblib.load(narrow_folder/'model.joblib').predict_proba(narrow_frame)[:, 1]
    narrow_saved = np.load(narrow_folder/'development-scores.npy')
    narrow_difference = float(np.max(np.abs(narrow_replay-narrow_saved)))
    if narrow_difference > 1e-12:
        raise ValueError('Full narrow-statistic tree replay differs')
    replays['narrow_tree'] = {'rows': len(narrow_saved), 'max_abs_diff': narrow_difference,
                             'model_sha256': digest(narrow_folder/'model.joblib')}
    metrics = {}
    for name in ('broad_neural', 'broad_tree'):
        expected = report['results']['neural_ensemble'] if name == 'broad_neural' else report['results']['tree']['metrics']
        score = scores_by_name[name]
        independent, queue = evaluate_scores(vmeta, vy, score)
        ap = manual_ap(vy, score)
        if independent['daily_review']['true_positives'] != expected['tp_daily10'] or abs(ap-expected['ap']) > 1e-12:
            raise ValueError('Independent broad queue/AP differs')
        metrics[name] = {'heap_daily10_TP': independent['daily_review']['true_positives'], 'manual_AP': ap, 'reviewed': len(queue)}
    comparisons = {}
    for name in ('mean', 'decay', 'gru', 'transformer', 'attention'):
        score = np.load(history_run/name/'ensemble.npy')
        for control in ('broad_neural', 'broad_tree'):
            comparison = ranking.compare(scores_by_name[control], score)
            comparison['block_sensitivity'] = block_interval(list(comparison['daily_net'].values()))
            comparisons[name+'_vs_'+control] = comparison
    result = {'status': 'posthoc_broad_controls_independently_verified', 'full_model_replays': 5,
              'feature_rows': len(raw), 'feature_dimensions': raw.shape[1], 'max_feature_abs_diff': max_difference,
              'model_replays': replays, 'metrics': metrics, 'comparisons': comparisons,
              'source_report_sha256': digest(broad_run/'report.json'), 'primary_audit_sha256': digest(Path(history_audit)/'report.json'),
              'data': provenance, 'final_2019_read': False, 'paid_calls': 0,
              'limit': 'Posthoc stronger challenge, not preregistered architecture selection or independent external confirmation'}
    write_json(output/'report.json', result)
    return result
