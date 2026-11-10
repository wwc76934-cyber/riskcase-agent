"""Audit V4 near-sequence sensitivity without scoring held-out test rows."""
import argparse
import json
from pathlib import Path
import time

import numpy as np
from sklearn.metrics import average_precision_score
import torch

from app.risk.behavior_semantic.baselines import numeric
from scripts.behavior_v3.review_blind_freeze import sha
from scripts.behavior_v4.run_study import Adapter, count_bands, event_ids, predict, read, selected_records


def development_labels(policy):
    with np.load(policy['split'],allow_pickle=False) as split:
        indices = split['development'].tolist()
    positions = {row:i for i,row in enumerate(indices)}
    labels = np.full(len(indices),-1,np.int8)
    idx = 0
    with Path(policy['records']).open(encoding='utf8') as stream:
        for line in stream:
            row = json.loads(line)
            if not row['valid_sequence']:
                continue
            if idx in positions:
                labels[positions[idx]] = row['is_fraud']
            idx += 1
    if len(labels)!=21219 or (labels<0).any() or labels.sum()!=23:
        raise ValueError('Unexpected development labels')
    return labels


def benchmark(run, policy, result):
    torch.set_num_threads(2)
    _,dev=selected_records(policy['records'],policy['split'])
    tick=time.monotonic()
    counts=count_bands([event_ids(row) for row in dev])
    preparation_seconds=time.monotonic()-tick
    empty=np.zeros((len(dev),0),np.float32)
    tick=time.monotonic()
    with np.load(run/'numeric_train_scaler.npz',allow_pickle=False) as scaler:
        numbers=((numeric(dev)-scaler['mean'])/scaler['scale']).astype(np.float32)
    numeric_seconds=time.monotonic()-tick
    times={}
    for panel, data in result['panels'].items():
        for name in data['ensembles']:
            key=f'{panel}/{name}/seed-42'
            path=run/key/'model.pt'
            if sha(path)!=result['models'][key]['checkpoint_sha256']:
                raise ValueError('V4 checkpoint changed: '+key)
            tick=time.monotonic()
            state=torch.load(path,map_location='cpu',weights_only=True)
            learned='embedding.weight' in state
            table=None if learned else state['table'].numpy()
            covariates=numbers if panel=='gate4' else empty
            model=Adapter(table,learned,covariates.shape[1])
            model.load_state_dict(state)
            pool=counts if learned else (counts@table).reshape(-1,4,128)
            score=predict(model,pool,covariates,policy['batch'])
            elapsed=time.monotonic()-tick
            saved=np.load(run/key/'development-scores.npy',allow_pickle=False)
            error=float(np.max(np.abs(score-saved)))
            if error>1e-6:
                raise ValueError('Independent V4 benchmark replay differs: '+key)
            times[key]={'reload_pool_infer_seconds':elapsed,'replay_max_abs_error':error}
    return {'rows':len(dev),'shared_event_counts_seconds':preparation_seconds,'shared_numeric_transform_seconds':numeric_seconds,
            'model_seconds':times,'scope':'Seed 42 per family. Shared input prep plus model reloading, frozen pooling where applicable and inference. Excludes JSONL disk loading.'}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--benchmark',action='store_true')
    args=parser.parse_args()
    policy=read(args.run/'protocol.json')
    results=read(args.run/'results.json')
    if results['test_membership_read'] or results['test_predictions_generated'] or len(results['models'])!=33:
        raise ValueError('Complete registered V4 development matrix required')
    for key in ('records','split','input_audit'):
        if sha(policy[key])!=policy[key+'_sha256']:
            raise ValueError('Frozen input changed: '+key)
    audit=Path(policy['input_audit'])
    mask_path=audit.with_name('development-near-training.npy')
    if sha(mask_path)!=read(audit)['near_mask_sha256']:
        raise ValueError('Development similarity mask changed')
    near=np.load(mask_path,allow_pickle=False)
    y=development_labels(policy)
    result={'near_train_rows':int(near.sum()),'near_train_positive':int(y[near].sum()),'models':{}}
    for panel,panel_data in results['panels'].items():
        subset={}
        for name in panel_data['ensembles']:
            scores=np.load(args.run/panel/name/'ensemble-development.npy',allow_pickle=False)
            subset[name]=float(average_precision_score(y[~near],scores[~near]))
        result['models'][panel]=subset
    result['test_membership_read']=False
    result['scope']='Same saved scores restricted to the existing non-near development subset; no refit.'
    result['results_sha256']=sha(args.run/'results.json')
    if args.benchmark:
        result['development_inference_benchmark']=benchmark(args.run,policy,results)
    with args.output.open('x',encoding='utf8') as stream:
        json.dump(result,stream,indent=2,allow_nan=False)


if __name__=='__main__':
    main()
