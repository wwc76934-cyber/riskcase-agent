"""Frozen, local-only OpenML 1597 detection comparison. No case or Agent claims."""
import argparse
from dataclasses import asdict
import hashlib
import json
import math
from pathlib import Path
import time

import joblib
import numpy as np
import pandas as pd
import torch
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.preprocessing import StandardScaler
from threadpoolctl import threadpool_limits

from app.risk.neural_model import seed_training
from app.risk.neural_v2 import NeuralConfig, TabularRiskNetwork, predict_tensors
from app.service.store import digest
from app.service.runtime import write_json

ROOT = Path(__file__).resolve().parents[2]
FEATURES = [f'V{i}' for i in range(1, 29)] + ['Amount']
MD5 = '178bcf9bb1f31a3dfe12d0e577884add'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def code_hashes():
    return {str(p.relative_to(ROOT)): sha(p) for p in [Path(__file__), ROOT/'app/risk/neural_v2.py', ROOT/'app/risk/neural_model.py', ROOT/'docs/EXTERNAL_DETECTION_PROTOCOL.md']}


def load(path):
    if hashlib.md5(path.read_bytes()).hexdigest() != MD5:
        raise ValueError('Fixed official file checksum required')
    columns = []
    with path.open(encoding='utf-8') as handle:
        for skip, line in enumerate(handle, 1):
            if line.lower().startswith('@attribute'):
                columns.append(line.split()[1].strip("'\""))
            if line.strip().lower() == '@data':
                break
        else:
            raise ValueError('Missing ARFF data header')
    if columns != ['Time'] + FEATURES + ['Class']:
        raise ValueError('Unexpected source schema')
    frame = pd.read_csv(path, skiprows=skip, names=columns)
    # ARFF's nominal Class values are single-quoted; strip only that syntax,
    # then reject every value outside the declared binary label domain.
    frame['Class']=pd.to_numeric(frame['Class'].astype(str).str.strip("'"),errors='raise')
    if len(frame) != 284807 or not np.isfinite(frame.to_numpy()).all() or not set(frame.Class.unique()) <= {0, 1}:
        raise ValueError('Source integrity check failed; no rows dropped')
    if frame.Time.min() < 0 or frame.Time.max() >= 172800:
        raise ValueError('Unexpected relative time range')
    return frame


def masks(frame):
    return dict(train=frame.Time < 86400, development=(frame.Time >= 86400) & (frame.Time < 129600), holdout=frame.Time >= 129600)


def metrics(labels, scores, row_ids):
    labels, scores, row_ids = np.asarray(labels), np.asarray(scores), np.asarray(row_ids)
    if scores.shape != labels.shape or not np.isfinite(scores).all() or not ((scores >= 0) & (scores <= 1)).all():
        raise ValueError('Finite aligned probability-like ranking scores required')
    order = np.lexsort((row_ids, -scores))
    review = {}
    for rate in (.001, .005, .01):
        k = max(1, math.ceil(len(labels) * rate)); captured = int(labels[order[:k]].sum())
        review[str(rate)] = dict(slots=k, captured=captured, precision=captured/k, recall=captured/int(labels.sum()))
    return dict(rows=len(labels), positives=int(labels.sum()), average_precision=float(average_precision_score(labels, scores)),
                roc_auc=float(roc_auc_score(labels, scores)), review_fraction=review)


def tensors(scaler, frame):
    values = scaler.transform(frame[FEATURES]).astype(np.float32)
    return torch.from_numpy(values), torch.empty((len(values), 0), dtype=torch.long)


def develop(path, output):
    if output.exists():
        raise ValueError('New output directory required; results are not overwritten')
    output.mkdir(parents=True)
    frame = load(path); split = masks(frame)
    train, dev = frame[split['train']], frame[split['development']]
    # Holdout labels are never scored or used to fit/choose during development.
    y, yd = train.Class.to_numpy(dtype=np.int64), dev.Class.to_numpy(dtype=np.int64)
    write_json(output/'preflight.json', dict(source_sha256=sha(path), code=code_hashes(),
               counts={k:int(v.sum()) for k,v in split.items()}, features=FEATURES, protocol='EXTERNAL_DETECTION_PROTOCOL.md'))
    scaler = StandardScaler().fit(train[FEATURES]); joblib.dump(scaler, output/'scaler.joblib')
    x, xd = scaler.transform(train[FEATURES]), scaler.transform(dev[FEATURES])
    results = {}; predictions = {}
    estimators = dict(logistic=LogisticRegression(C=1, class_weight='balanced', max_iter=1000, random_state=42),
        histogram_boosting=HistGradientBoostingClassifier(max_iter=150, learning_rate=.05, max_leaf_nodes=31, l2_regularization=1, early_stopping=False, random_state=42))
    weights = np.where(y == 1, len(y)/(2*y.sum()), len(y)/(2*(len(y)-y.sum())))
    for name, model in estimators.items():
        start = time.perf_counter()
        if name == 'histogram_boosting': model.fit(x, y, sample_weight=weights)
        else: model.fit(x, y)
        train_s = time.perf_counter()-start; start = time.perf_counter(); scores = model.predict_proba(xd)[:,1]
        results[name] = dict(**metrics(yd, scores, dev.index), train_seconds=train_s, predict_seconds=time.perf_counter()-start)
        predictions[name] = scores; joblib.dump(model, output/(name+'.joblib'))
        print(json.dumps(dict(model=name, development=results[name])), flush=True)
    config = NeuralConfig(width=64, blocks=2, dropout=.1, weight_decay=.0001, positive_weight=min((len(y)-y.sum())/y.sum(), 50), engineered=False)
    torch.set_num_threads(4)
    tx, td = tensors(scaler, train), tensors(scaler, dev); labels = torch.from_numpy(y.astype(np.float32))
    neural = []; durations = []
    for seed in (42, 43, 44):
        seed_training(seed); model = TabularRiskNetwork(len(FEATURES), [], config)
        optimizer = torch.optim.AdamW(model.parameters(), lr=config.learning_rate, weight_decay=config.weight_decay)
        loss_fn = torch.nn.BCEWithLogitsLoss(pos_weight=torch.tensor(float(config.positive_weight)))
        start = time.perf_counter()
        for epoch in range(10):
            model.train(); order = torch.randperm(len(y))
            for offset in range(0, len(y), config.batch_size):
                batch = order[offset:offset+config.batch_size]
                optimizer.zero_grad(set_to_none=True); loss = loss_fn(model(tx[0][batch], tx[1][batch]), labels[batch])
                if not torch.isfinite(loss): raise ValueError('Nonfinite training loss')
                loss.backward(); optimizer.step()
        train_s = time.perf_counter()-start; durations.append(train_s)
        start=time.perf_counter(); scores=predict_tensors(model,td); neural.append(scores)
        results[f'residual_seed_{seed}']=dict(**metrics(yd,scores,dev.index),train_seconds=train_s,predict_seconds=time.perf_counter()-start)
        torch.save(model.state_dict(), output/f'residual-{seed}.pt')
        print(json.dumps(dict(model=f'residual_seed_{seed}', development=results[f'residual_seed_{seed}'])),flush=True)
    predictions['residual_ensemble']=np.mean(neural,axis=0)
    results['residual_ensemble']=dict(**metrics(yd,predictions['residual_ensemble'],dev.index),train_seconds=sum(durations),predict_seconds=sum(results[f'residual_seed_{s}']['predict_seconds'] for s in (42,43,44)))
    winner=max(predictions,key=lambda name:results[name]['average_precision'])
    # Each serialized model is local code output, never an untrusted download.
    frozen=dict(protocol=code_hashes(),source_sha256=sha(path),features=FEATURES,config=asdict(config),selected_family=winner,
                development=results,artifacts={p.name:sha(p) for p in output.iterdir() if p.is_file()},holdout_evaluated=False)
    write_json(output/'frozen-development.json',frozen)
    print(json.dumps(dict(state='frozen_before_holdout',selected_family=winner,manifest_hash=digest(frozen))),flush=True)


def evaluate(path, output):
    target=output/'holdout.json'
    if target.exists():raise ValueError('Holdout already evaluated; preserve first result')
    frozen=json.loads((output/'frozen-development.json').read_text(encoding='utf-8'))
    if frozen['protocol']!=code_hashes() or frozen['source_sha256']!=sha(path):raise ValueError('Frozen source or code changed')
    if any(sha(output/name)!=value for name,value in frozen['artifacts'].items()):raise ValueError('Model or development artifact changed')
    frame=load(path);test=frame[masks(frame)['holdout']];y=test.Class.to_numpy(dtype=np.int64)
    scaler=joblib.load(output/'scaler.joblib');x=scaler.transform(test[FEATURES]);result={};scores={}
    for name in ('logistic','histogram_boosting'):
        model=joblib.load(output/(name+'.joblib'));start=time.perf_counter();scores[name]=model.predict_proba(x)[:,1]
        duration=time.perf_counter()-start;result[name]=dict(**metrics(y,scores[name],test.index),predict_seconds=duration)
    torch.set_num_threads(4);config=NeuralConfig(**frozen['config']);tt=tensors(scaler,test);neural=[];elapsed=0
    for seed in (42,43,44):
        model=TabularRiskNetwork(len(FEATURES),[],config);model.load_state_dict(torch.load(output/f'residual-{seed}.pt',map_location='cpu',weights_only=True))
        start=time.perf_counter();s=predict_tensors(model,tt);duration=time.perf_counter()-start;elapsed+=duration;neural.append(s)
        result[f'residual_seed_{seed}']=dict(**metrics(y,s,test.index),predict_seconds=duration)
    scores['residual_ensemble']=np.mean(neural,axis=0)
    result['residual_ensemble']=dict(**metrics(y,scores['residual_ensemble'],test.index),predict_seconds=elapsed)
    pd.DataFrame(dict(source_row=test.index,**scores)).to_csv(output/'holdout-predictions.csv',index=False)
    report=dict(protocol_hash=digest(frozen),selected_before_holdout=frozen['selected_family'],results=result,
                source_sha256=frozen['source_sha256'],data_kind='public_described_real_anonymized_transactions',
                limits=['two_days_one_source','upstream_PCA_fit_scope_unknown','no_entity_or_investigation_evidence','labels_are_dataset_annotations_not_independent_adjudication'],
                predictions_sha256=sha(output/'holdout-predictions.csv'))
    write_json(target,report);print(json.dumps(report),flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('action',choices=['develop','holdout'])
    parser.add_argument('--source',type=Path,default=Path('data/raw/openml-creditcard-v1/creditcard.arff'))
    parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    with threadpool_limits(limits=4):
        (develop if args.action=='develop' else evaluate)(args.source,args.output)


if __name__=='__main__':main()
