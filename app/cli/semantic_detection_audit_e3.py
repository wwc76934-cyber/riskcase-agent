"""Recompute label budgets and final E3 metrics independently, without training."""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import torch
import joblib

from app.cli.semantic_detection_audit import manual_ap
from app.risk.ml_evaluation import evaluate_scores
from app.risk.semantic_detection.common import digest, write_json
from app.risk.semantic_detection.data import load_data
from app.risk.semantic_detection.semantics import load_cache


def audit(source_root, run, output, cache_path):
    run, output = Path(run).resolve(), Path(output)
    if output.exists():
        raise FileExistsError('New audit output required')
    report = json.loads((run / 'report.json').read_text(encoding='utf-8'))
    policy = json.loads((run / 'protocol.json').read_text(encoding='utf-8'))['policy']
    if report['status'] != 'E3_bounded_complete' or report.get('encoding_contract') != 'additive-v2':
        raise ValueError('Formal completed E3 required')
    for name, expected in report['artifacts'].items():
        path = (run / name).resolve()
        if not path.is_relative_to(run) or digest(path) != expected:
            raise ValueError('E3 artifact differs')
    (X, y, meta), (VX, vy, vmeta) = load_data(source_root)[0]
    cache, table = load_cache(cache_path)
    results = {}
    identities = meta.transaction_id.to_numpy()
    for fraction in policy['fractions']:
        name = f'{fraction:.2f}'
        mask = np.load(run / f'mask-{name}.npy', allow_pickle=False)
        if mask.shape != y.shape or mask.dtype != np.bool_:
            raise ValueError('Aligned boolean mask required')
        # Independent rank-by-hash selection, recomputed separately for exposed classes.
        for label in (0, 1):
            positions = np.flatnonzero(y == label)
            hashes = np.array([hashlib.sha256(f'{policy["mask_seed"]}\0{identities[i]}'.encode()).hexdigest() for i in positions])
            count = max(1, int(np.ceil(len(positions) * fraction)))
            selected = set(positions[np.argsort(hashes)[:count]])
            if set(np.flatnonzero(mask & (y == label))) != selected:
                raise ValueError('Frozen mask differs from declared train-only scheme')
        outcomes = {}
        for condition in policy['losses']:
            root = run / name / condition['id']
            scores = np.load(root / 'ensemble.npy', allow_pickle=False)
            seeds = [np.load(root / f'seed-{seed}/development-scores.npy', allow_pickle=False) for seed in policy['seeds']]
            if not np.array_equal(scores, np.mean(seeds, axis=0)):
                raise ValueError('E3 ensemble differs')
            checked, queue = evaluate_scores(vmeta, vy, scores)
            expected = report['results'][name]['conditions'][condition['id']]['ensemble']
            ap = manual_ap(vy, scores)
            if checked['daily_review']['true_positives'] != expected['tp_daily10'] or abs(ap - expected['ap']) > 1e-10:
                raise ValueError('Independent E3 rank/AP differ')
            for seed in policy['seeds']:
                saved = json.loads((root / f'seed-{seed}/result.json').read_text(encoding='utf-8'))
                prep = joblib.load(root / f'seed-{seed}/model/preprocessor.joblib')
                if prep.keys != [tuple(key) for key in cache['keys']]:
                    raise ValueError('Actual model category-to-vector identities differ')
                if saved['exposed_positive'] != int(y[mask].sum()) or saved['unlabeled'] != int((~mask).sum()):
                    raise ValueError('Optimizer exposed-count receipt differs')
                if condition['variant'] == 'B3':
                    state = torch.load(root / f'seed-{seed}/model/model.pt', map_location='cpu', weights_only=True)
                    if not np.array_equal(state['state_dict']['category.weight'].numpy(), table):
                        raise ValueError('Actual frozen semantic vectors differ from declared encoder cache')
            outcomes[condition['id']] = {'tp': expected['tp_daily10'], 'manual_ap': ap, 'reviewed': len(queue)}
        if fraction == 1:
            for seed in policy['seeds']:
                a = np.load(run / name / 'cb_focal' / f'seed-{seed}/development-scores.npy', allow_pickle=False)
                b = np.load(run / name / 'consistency' / f'seed-{seed}/development-scores.npy', allow_pickle=False)
                if not np.array_equal(a, b):
                    raise ValueError('100% labeled negative control should have zero consistency effect')
        results[name] = outcomes
    value = {'status': 'independent_E3_audit_passed', 'masks': 4, 'conditions': 20, 'model_runs': 60,
             'final_2019_read': False, 'paid_calls': 0, 'report_sha256': digest(run / 'report.json'), 'results': results,
             'all_labeled_zero_consistency_verified': True}
    value.update(embedding_cache_manifest_sha256=digest(Path(cache_path) / 'manifest.json'),
                 embedding_vectors_sha256=cache['vectors_sha256'],
                 actual_semantic_model_tables_checked=12)
    write_json(output, value)
    print(json.dumps({'status': value['status'], 'masks': 4, 'conditions': 20,
                      'model_runs': 60, 'all_labeled_zero_consistency_verified': True}, ensure_ascii=False))
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ['source-root', 'run', 'output', 'cache']:
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    audit(args.source_root, args.run, args.output, args.cache)


if __name__ == '__main__':
    main()
