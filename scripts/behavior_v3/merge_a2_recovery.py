"""Combine untouched A2 checkpoints with a same-protocol Transformer retry."""
import argparse
import json
from pathlib import Path

import numpy as np

from app.risk.behavior_semantic.study import development_threshold, metrics, read_records, sha, write


def read(path):
    return json.loads(path.read_text(encoding='utf8'))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--original', type=Path, required=True)
    parser.add_argument('--retry', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--original-fingerprints', type=Path)
    args = parser.parse_args()
    original, retry, output = args.original, args.retry, args.output
    policy = read(original / 'protocol.json')
    retry_policy = read(retry / 'protocol.json')
    retry_result = read(retry / 'development-results.json')
    retry_manifest = read(retry / 'all-training-complete.json')
    original_fingerprints = read(args.original_fingerprints)['original_instances'] if args.original_fingerprints else None
    subset_fields = {'models', 'selected_instances', 'classical_candidate_fits',
                     'max_neural_training_epochs_total', 'recovery_of', 'recovery_reason'}
    if any(retry_policy[key] != value for key, value in policy.items() if key not in subset_fields):
        raise ValueError('Transformer retry changed frozen settings')
    if (policy['release_final_test'] or retry_result['final_test_predictions_generated']
            or retry_policy['models'] != ['transformer'] or retry_manifest['instances'] != len(policy['seeds'])):
        raise ValueError('Invalid development-only Transformer retry')
    for name in ('records', 'split'):
        if sha(policy[name]) != policy[name + '_sha256']:
            raise ValueError('Frozen input changed: ' + name)
    rows, _ = read_records(policy['records'])
    with np.load(policy['split'], allow_pickle=False) as split:
        y = np.asarray([rows[int(i)]['is_fraud'] for i in split['development']])

    records, artifacts, scores = {}, {}, {}
    for family in policy['models']:
        for seed in policy['seeds']:
            key = f'{family}/seed-{seed}'
            folder = (retry if family == 'transformer' else original) / key
            record = read(folder / 'training.json')
            if record['test_seen_by_trainer'] or record['seed'] != seed:
                raise ValueError('Invalid saved checkpoint: ' + key)
            neural = family in {'gru', 'transformer'}
            model_names = ['model.pt', 'preparation.joblib'] if neural else ['model.joblib']
            hashes = {name: sha(folder / name) for name in [*model_names, 'development-scores.npy', 'training.json']}
            if family != 'transformer' and original_fingerprints is not None and original_fingerprints[key] != hashes:
                raise ValueError('Original checkpoint fingerprint changed: ' + key)
            artifacts[key] = {'kind': 'torch' if neural else 'classical', 'folder': str(folder), 'sha256': hashes}
            records[key] = record
            scores[key] = np.load(folder / 'development-scores.npy', allow_pickle=False)
            if scores[key].shape != y.shape or not np.isfinite(scores[key]).all():
                raise ValueError('Invalid development scores: ' + key)

    output.mkdir(parents=True, exist_ok=False)
    write(output / 'protocol.json', policy)
    summaries = {}
    for family in policy['models']:
        mean = np.mean([scores[f'{family}/seed-{seed}'] for seed in policy['seeds']], axis=0)
        summaries[family] = metrics(y, mean, development_threshold(y, mean))
        folder = output / family
        folder.mkdir()
        np.save(folder / 'ensemble-development.npy', mean)
        for seed in policy['seeds']:
            seed_folder = folder / f'seed-{seed}'
            seed_folder.mkdir()
            np.save(seed_folder / 'development-scores.npy', scores[f'{family}/seed-{seed}'])
    write(output / 'all-training-complete.json', {'instances': len(artifacts), 'artifacts': artifacts})
    write(output / 'development-results.json', {
        'status': 'A2_development_comparison_complete_test_still_sealed',
        'records': records, 'development_ensembles': summaries, 'final_test_predictions_generated': False,
        'original_protocol_sha256': sha(original / 'protocol.json'),
        'retry_results_sha256': sha(retry / 'development-results.json'),
        'scope': 'Static grouped development. The interrupted first Transformer fit is retained separately.',
    })


if __name__ == '__main__':
    main()
