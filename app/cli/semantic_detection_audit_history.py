"""Audit every training edge and compare history to matched full-label E3 baseline."""
import argparse
import json
from pathlib import Path
import sqlite3

import numpy as np

from app.cli.semantic_detection_audit import manual_ap
from app.risk.ml_evaluation import evaluate_scores
from app.risk.semantic_detection.common import digest, write_json
from app.risk.semantic_detection.data import load_data
from app.risk.semantic_detection.evaluation import Ranking


def audit(source_root, history_path, e3_path, output):
    history_path, e3_path, output = Path(history_path).resolve(), Path(e3_path).resolve(), Path(output)
    if output.exists():
        raise FileExistsError('New audit output required')
    report = json.loads((history_path / 'report.json').read_text(encoding='utf-8'))
    e3 = json.loads((e3_path / 'report.json').read_text(encoding='utf-8'))
    history_policy = json.loads((history_path / 'protocol.json').read_text(encoding='utf-8'))['policy']
    e3_policy = json.loads((e3_path / 'protocol.json').read_text(encoding='utf-8'))['policy']
    if (history_policy['epochs'] != e3_policy['epochs'] or history_policy['seeds'] != e3_policy['seeds']
            or history_policy['config'] != e3_policy['config']):
        raise ValueError('Baseline training budget/configuration is not matched')
    k = history_policy['neighbors']
    if report['status'] != 'E2_history_bounded_complete' or e3['status'] != 'E3_bounded_complete':
        raise ValueError('Completed history and baseline receipts required')
    for name, expected in report['artifacts'].items():
        path = (history_path / name).resolve()
        if not path.is_relative_to(history_path) or digest(path) != expected:
            raise ValueError('History material changed')
    (X, y, meta), (VX, vy, vmeta) = (loaded := load_data(source_root))[0]
    for directory in (history_path, e3_path):
        saved_data = json.loads((directory / 'data.json').read_text(encoding='utf-8'))
        if any(saved_data[key] != loaded[1][key] for key in ('manifest_sha256', 'features_sha256', 'labels_sha256')):
            raise ValueError('Matched experiment source fingerprints differ')
    ranking = Ranking(vmeta, vy)
    neighbors = np.load(history_path / 'train-neighbors.npy', allow_pickle=False)
    ages = np.load(history_path / 'train-ages.npy', allow_pickle=False)
    if neighbors.shape != (len(meta), k) or ages.shape != neighbors.shape:
        raise ValueError('Frozen bounded graph shape differs')
    times = meta.event_time.to_numpy(dtype='datetime64[ns]').astype(np.int64)
    users = meta.user_id.to_numpy()
    for start in range(0, len(meta), 8192):
        idx = neighbors[start:start + 8192]; valid = idx >= 0; safe = np.maximum(idx, 0)
        current = np.arange(start, start + len(idx))[:, None]
        if (idx[valid] >= len(meta)).any() or (idx < -1).any():
            raise ValueError('Evaluation/future graph index outside train scope')
        if (users[safe][valid] != np.broadcast_to(users[current], idx.shape)[valid]).any():
            raise ValueError('Cross-user edge')
        delta = ((times[current] - times[safe]) / 1e9).astype(np.float32)
        if (delta[valid] <= 0).any() or not np.array_equal(delta[valid], ages[start:start + len(idx)][valid]):
            raise ValueError('Equal-time/future edge or age mismatch')
    # Independently reconstruct recent-neighbor identity/order from SQLite for 96 targets.
    sample = np.random.default_rng(991).choice(len(meta), 96, replace=False)
    with sqlite3.connect(':memory:') as db:
        db.execute('CREATE TABLE events(position INTEGER,id TEXT,user TEXT,stamp TEXT)')
        db.executemany('INSERT INTO events VALUES(?,?,?,?)',
                       ((i, str(identity), str(user), str(stamp)) for i, (identity, user, stamp) in
                        enumerate(zip(meta.transaction_id, meta.user_id, meta.event_time))))
        db.execute('CREATE INDEX entity_time ON events(user,stamp,id)')
        for pos in sample:
            row = meta.iloc[pos]
            expected = [r[0] for r in db.execute('SELECT position FROM events WHERE user=? AND stamp<? ORDER BY stamp DESC,id DESC LIMIT ?', (row.user_id, row.event_time, k))]
            if neighbors[pos, :len(expected)].tolist() != expected or (neighbors[pos, len(expected):] != -1).any():
                raise ValueError('Independent last-K identity/order differs')
    base = np.load(e3_path / '1.00/bce/ensemble.npy', allow_pickle=False)
    comparisons = {}; results = {}
    for mode, saved in report['results'].items():
        scores = np.load(history_path / mode / 'ensemble.npy', allow_pickle=False)
        seeds = [np.load(history_path / mode / f'seed-{seed}/development-scores.npy', allow_pickle=False) for seed in (42, 43, 44)]
        if not np.array_equal(scores, np.mean(seeds, axis=0)):
            raise ValueError('History seed ensemble differs')
        checked, queue = evaluate_scores(vmeta, vy, scores)
        if checked['daily_review']['true_positives'] != saved['ensemble']['tp_daily10'] or abs(manual_ap(vy, scores) - saved['ensemble']['ap']) > 1e-10:
            raise ValueError('Independent history metric differs')
        comparisons[mode + '_vs_matched_flat'] = ranking.compare(base, scores)
        results[mode] = {'metrics': saved['ensemble'], 'independent_reviewed': len(queue),
                         'seed_tp': [r['metrics']['tp_daily10'] for r in saved['seeds']],
                         'parameters': saved['seeds'][0]['parameters'],
                         'training_seconds': [r['train_seconds'] for r in saved['seeds']],
                         'inference_seconds': [r['inference_seconds'] for r in saved['seeds']],
                         'peak_process_bytes': max(r['peak_process_bytes'] for r in saved['seeds'])}
    comparisons['attention_vs_mean'] = ranking.compare(np.load(history_path / 'mean/ensemble.npy', allow_pickle=False),
                                                       np.load(history_path / 'attention/ensemble.npy', allow_pickle=False))
    value = {'status': 'independent_history_audit_passed', 'training_edges_checked': int((neighbors >= 0).sum()),
             'sqlite_reconstructed_targets': len(sample), 'training_evaluation_nodes': 0,
             'matched_flat': e3['results']['1.00']['conditions']['bce']['ensemble'],
             'results': results, 'comparisons': comparisons, 'graph': report['graph'],
             'history_report_sha256': digest(history_path / 'report.json'), 'e3_report_sha256': digest(e3_path / 'report.json'),
             'final_2019_read': False, 'paid_calls': 0, 'limits': report['limits']}
    write_json(output, value)
    print(json.dumps({'status': value['status'], 'edges': value['training_edges_checked'],
                      'matched_flat_tp': value['matched_flat']['tp_daily10'],
                      'history_tp': {n: r['metrics']['tp_daily10'] for n, r in results.items()}}, ensure_ascii=False))
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ['source-root', 'history', 'e3', 'output']:
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    audit(args.source_root, args.history, args.e3, args.output)


if __name__ == '__main__':
    main()
