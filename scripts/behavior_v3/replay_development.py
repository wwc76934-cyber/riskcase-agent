"""Independently reload a completed local model matrix and audit development scores."""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import time

import joblib
import numpy as np
import torch

from app.risk.behavior_semantic.baselines import events
from app.risk.behavior_semantic.study import development_threshold, metrics, read_records, sha, write
from scripts.behavior_v3.final_evaluate import manual_ap, predict_artifact


def read(path):
    return json.loads(path.read_text(encoding='utf8'))


def panel(y, score):
    count = int(y.sum())
    value = {'rows':len(y), 'positives':count}
    if count:
        value.update(metrics(y, score, development_threshold(y, score)))
    return value


def classical_size(folder):
    artifact = joblib.load(folder / 'model.joblib')
    model = artifact.model
    if hasattr(model, 'estimators_'):
        return {'tree_nodes':sum(int(tree.tree_.node_count) for tree in model.estimators_),
                'trees':len(model.estimators_), 'linear_parameters':None}
    return {'linear_parameters':int(model.coef_.size + model.intercept_.size)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--input-audit', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--expected-instances', type=int, required=True)
    args = parser.parse_args()
    run, audit_dir = args.run, args.input_audit
    policy = read(run/'protocol.json')
    report = read(run/'development-results.json')
    manifest = read(run/'all-training-complete.json')
    audit = read(audit_dir/'audit.json')
    if (report['final_test_predictions_generated'] or audit['test_predictions_generated']
            or len(manifest['artifacts']) != args.expected_instances or manifest['instances'] != args.expected_instances):
        raise ValueError('Development-only complete matrix required')
    for name in ('records','split'):
        if sha(policy[name]) != policy[name+'_sha256'] or audit[name+'_sha256'] != policy[name+'_sha256']:
            raise ValueError('Audited input changed: ' + name)
    mask_path = audit_dir/'development-near-training.npy'
    if sha(mask_path) != audit['near_mask_sha256']:
        raise ValueError('Similarity mask changed')
    near = np.load(mask_path,allow_pickle=False)
    rows, _ = read_records(policy['records'])
    with np.load(policy['split'],allow_pickle=False) as split:
        train, dev = ([rows[int(i)] for i in split[name]] for name in ('train','development'))
    y = np.array([r['is_fraud'] for r in dev])
    frequency = Counter(x for row in train for x in set(events(row)[-256:]))
    masks = {'near_train':near, 'without_detected_near_train':~near}
    for name, low, high in [('unseen',0,0), ('rare_1_5',1,5), ('rare_6_20',6,20)]:
        masks[name] = np.array([any(low <= frequency[x] <= high for x in events(row)[-256:]) for row in dev])
    torch.set_num_threads(2)
    models, family_scores = {}, defaultdict(list)
    for key, spec in manifest['artifacts'].items():
        folder = Path(spec['folder'])
        names = ['model.joblib'] if (folder/'model.joblib').exists() else ['model.pt','preparation.joblib']
        fingerprints = {n:sha(folder/n) for n in [*names,'training.json','development-scores.npy']}
        if spec.get('sha256') and fingerprints != spec['sha256']:
            raise ValueError('Saved artifact changed: ' + key)
        start, cpu = time.monotonic(), time.process_time()
        replay_spec = {**spec,'history_control_revision':policy.get('history_control_revision')}
        score = predict_artifact(replay_spec, dev)
        elapsed, cpu_elapsed = time.monotonic()-start, time.process_time()-cpu
        saved = np.load(folder/'development-scores.npy',allow_pickle=False)
        error = float(np.max(np.abs(score-saved)))
        if not np.isfinite(score).all() or error > 1e-6:
            raise ValueError('Independent development replay differs: ' + key)
        record = read(folder/'training.json')
        value = panel(y, score)
        if abs(manual_ap(y,score)-value['AP']) > 1e-12:
            raise ValueError('Independent AP differs: ' + key)
        size = classical_size(folder) if names == ['model.joblib'] else {
            'parameters':record['parameters'], 'trainable_parameters':record['trainable_parameters']}
        models[key] = {'development':value, 'score_replay_max_abs_error':error, 'sha256':fingerprints,
            'parameter_size':size, 'training_wall_seconds':record.get('wall_seconds', record.get('train_wall_seconds')),
            'training_CPU_seconds':record.get('CPU_seconds', record.get('train_CPU_seconds')),
            'training_timing_scope':record.get('timing_scope','full saved fit'),
            'reload_and_inference_wall_seconds':elapsed, 'reload_and_inference_CPU_seconds':cpu_elapsed}
        family_scores[key.split('/')[0]].append(score)
        print(key, 'development_replay_error', error, flush=True)
    families = {}
    for family, scores in family_scores.items():
        score = np.mean(scores,axis=0)
        budget = int(np.ceil(.01*len(y)))
        top = np.lexsort((np.random.default_rng(20261104).random(len(y)),-score))[:budget]
        strata = {}
        for name, mask in masks.items():
            in_queue = top[mask[top]]
            strata[name] = {**panel(y[mask],score[mask]),'rows_in_global_top1pct':len(in_queue),
                            'positives_in_global_top1pct':int(y[in_queue].sum())}
        families[family] = {'development':panel(y,score), 'strata':strata,
            'score_quantiles_by_label':{str(c):np.quantile(score[y==c],[0,.25,.5,.75,.95,.99,1]).tolist() for c in (0,1)}}
    args.output.mkdir(parents=True,exist_ok=False)
    write(args.output/'replay.json',{'status':'full_development_replay_verified', 'instances':len(models),
        'input_audit_sha256':sha(audit_dir/'audit.json'), 'development_results_sha256':sha(run/'development-results.json'),
        'models':models,'ensembles':families,'test_predictions_generated':False,
        'scope':'Slices use fixed saved rankings restricted to each stratum. Top 1% budgets are recalculated within strata. Rare strata overlap; frequencies use training suffixes.'})


if __name__ == '__main__':
    main()
