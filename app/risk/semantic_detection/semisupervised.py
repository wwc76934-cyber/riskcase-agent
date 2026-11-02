"""Frozen full-train label-budget matrix, hidden labels stripped before fit."""
import hashlib
import json
from pathlib import Path
import time

import numpy as np
from threadpoolctl import threadpool_limits

from .common import digest, fingerprint, write_json
from .data import label_mask, load_data
from .evaluation import Ranking
from .experiment import fit
from .model import DetectorConfig
from .semantics import load_cache


def run(source_root, cache_path, protocol_path, output):
    output = Path(output); output.mkdir(parents=True, exist_ok=False)
    policy = json.loads(Path(protocol_path).read_text(encoding='utf-8'))
    if policy.get('encoding_contract') != 'additive-v2':
        raise ValueError('Formal E3 requires additive-v2 policy')
    write_json(output / 'protocol.json', {'policy': policy, 'policy_sha256': digest(protocol_path),
               'code': fingerprint(Path(__file__).parent), 'final_2019_read': False, 'paid_calls': 0})
    (X, y, meta), (VX, vy, vmeta) = (loaded := load_data(source_root))[0]
    write_json(output / 'data.json', loaded[1])
    cache, table = load_cache(cache_path)
    write_json(output / 'cache.json', cache)
    ranking = Ranking(vmeta, vy)
    started = time.monotonic(); results = {}; frozen_masks = {}
    for fraction in policy['fractions']:
        name = f'{fraction:.2f}'
        mask, exposed = label_mask(meta.transaction_id.tolist(), y, fraction, policy['mask_seed'])
        mask_path = output / f'mask-{name}.npy'; np.save(mask_path, mask)
        frozen_masks[name] = {'mask_sha256': digest(mask_path), 'negative': int((exposed == 0).sum()),
                              'positive': int((exposed == 1).sum()), 'unlabeled': int((exposed < 0).sum())}
    write_json(output / 'frozen-masks.json', frozen_masks)
    # All masks are saved before any metric is calculated.
    try:
        for fraction in policy['fractions']:
            name = f'{fraction:.2f}'; mask = np.load(output / f'mask-{name}.npy', allow_pickle=False)
            exposed = np.where(mask, y, -1).astype(np.int8)
            condition_results = {}; ensemble_scores = {}
            for condition in policy['losses']:
                scores = []; reports = []
                for seed in policy['seeds']:
                    destination = output / name / condition['id'] / f'seed-{seed}'
                    with threadpool_limits(limits=4):
                        _, report = fit(DetectorConfig(variant=condition['variant'], **policy['config']), X, exposed,
                                        cache['keys'], table, seed=seed, epochs=policy['epochs'], output=destination,
                                        ranking=ranking, validation=VX, channels=VX.channel.to_numpy(),
                                        loss_kind=condition['loss'], consistency_weight=condition['consistency'])
                    reports.append(report); scores.append(np.load(destination / 'development-scores.npy', allow_pickle=False))
                mean = np.mean(scores, axis=0); ensemble_scores[condition['id']] = mean
                np.save(output / name / condition['id'] / 'ensemble.npy', mean)
                condition_results[condition['id']] = {'seeds': reports, 'ensemble': ranking.metrics(mean, VX.channel.to_numpy())}
                write_json(output / name / condition['id'] / 'summary.json', condition_results[condition['id']])
            comparisons = {f'{candidate}_vs_{base}': ranking.compare(ensemble_scores[base], ensemble_scores[candidate])
                           for candidate, base in [('focal', 'bce'), ('cb_focal', 'focal'), ('consistency', 'cb_focal'),
                                                   ('semantic_consistency', 'consistency')]}
            results[name] = {'conditions': condition_results, 'comparisons': comparisons}
            write_json(output / f'results-{name}.json', results[name])
        report = {'status': 'E3_bounded_complete', 'scope': '2018 observed development', 'final_2019_read': False,
                  'encoding_contract': 'additive-v2',
                  'masks': frozen_masks, 'results': results, 'elapsed_seconds': time.monotonic() - started,
                  'limits': policy['limits'], 'artifacts': fingerprint(output)}
        write_json(output / 'report.json', report)
        return report
    except Exception as exc:
        write_json(output / 'failure.json', {'type': type(exc).__name__, 'message': str(exc)})
        raise
