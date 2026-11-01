"""Independent SQLite daily ranking + threshold-group AP, no training or API calls."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3

import numpy as np

from app.risk.semantic_detection.adapter import checked_run
from app.risk.semantic_detection.common import digest, write_json
from app.risk.semantic_detection.data import load_data
from app.risk.semantic_detection.model import load_model


def manual_ap(y, scores):
    y, scores = np.asarray(y), np.asarray(scores)
    if not y.sum():
        return None
    order = np.argsort(-scores, kind='stable')
    sorted_scores, cumulative = scores[order], np.cumsum(y[order])
    ends = np.r_[np.flatnonzero(np.diff(sorted_scores) != 0), len(y) - 1]
    group_positive = np.diff(np.r_[0, cumulative[ends]])
    return float(np.sum(group_positive / y.sum() * cumulative[ends] / (ends + 1)))


def audit(source_root, run, output):
    run, output = Path(run).resolve(), Path(output)
    if output.exists():
        raise FileExistsError('New audit output required')
    report = checked_run(run)
    (_, _, _), (X, y, meta) = load_data(source_root)[0]
    results = {}
    identities = meta.transaction_id.to_numpy(dtype=str)
    index = {identity: i for i, identity in enumerate(identities)}
    for variant, record in report['results'].items():
        scores = np.load(run / variant / 'ensemble.npy', allow_pickle=False)
        with sqlite3.connect(':memory:') as db:
            db.execute('CREATE TABLE scores(id TEXT PRIMARY KEY,user TEXT,stamp TEXT,score REAL,tie TEXT)')
            # No labels in this table: selection is frozen before outcomes are consulted.
            db.executemany('INSERT INTO scores VALUES(?,?,?,?,?)',
                ((str(i), str(u), str(t), float(s), hashlib.sha256(f'riskcase-review-ties-v1\0{i}'.encode()).hexdigest())
                 for i, u, t, s in zip(identities, meta.user_id, meta.event_time, scores)))
            rows = db.execute('''SELECT id,user,stamp,score,tie,rank FROM
                (SELECT *,ROW_NUMBER() OVER(PARTITION BY substr(stamp,1,10) ORDER BY score DESC,tie DESC,id DESC) rank FROM scores)
                WHERE rank<=10 ORDER BY substr(stamp,1,10),rank''').fetchall()
        selected = [index[r[0]] for r in rows]
        tp = int(y[selected].sum()); ap = manual_ap(y, scores)
        expected = record['ensemble']
        if tp != expected['tp_daily10'] or len(rows) != expected['reviewed'] or abs(ap - expected['ap']) > 1e-10:
            raise ValueError('Independent SQLite count or manual AP differs')
        differences = []
        pos = np.random.default_rng(739).choice(len(X), 96, replace=False)
        seed_scores = []
        for seed in (42, 43, 44):
            root = run / variant / f'seed-{seed}'
            saved = np.load(root / 'development-scores.npy', allow_pickle=False)
            predicted = load_model(root / 'model').predict(X.iloc[pos])
            delta = float(np.max(np.abs(predicted - saved[pos])))
            if delta > 1e-6:
                raise ValueError('Independent sampled model replay differs')
            differences.append(delta); seed_scores.append(saved)
        if not np.array_equal(scores, np.mean(seed_scores, axis=0)):
            raise ValueError('Declared equal-seed ensemble differs')
        results[variant] = {'sqlite_tp': tp, 'sqlite_reviewed': len(rows), 'manual_ap': ap,
                            'reloaded_samples_each_seed': len(pos), 'reload_max_abs_differences': differences,
                            'frozen_queue_sha256': hashlib.sha256(json.dumps(rows, separators=(',', ':')).encode()).hexdigest()}
    value = {'status': 'independent_E1_audit_passed', 'scope': '2018 observed development', 'final_2019_read': False,
             'run_report_sha256': digest(run / 'report.json'), 'variants': results, 'paid_calls': 0}
    write_json(output, value)
    print(json.dumps(value, ensure_ascii=False))
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    audit(args.source_root, args.run, args.output)


if __name__ == '__main__':
    main()
