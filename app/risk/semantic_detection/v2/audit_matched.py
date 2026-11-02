"""Independent heap/AP/preprocessor/reload audit of completed matched experiments."""
import hashlib
import json
from pathlib import Path

import numpy as np

from app.risk.ml_evaluation import evaluate_scores
from app.risk.semantic_detection.common import digest,write_json
from app.risk.semantic_detection.data import load_data
from .matched import load
from app.risk.ml_models import MODEL_COLUMNS


def manual_ap(y,scores):
    order=np.argsort(-np.asarray(scores),kind='stable');s=np.asarray(scores)[order];labels=np.asarray(y)[order]
    cumulative=np.cumsum(labels);last=np.flatnonzero(np.r_[s[:-1]!=s[1:],True])
    increments=np.diff(np.r_[0,cumulative[last]])
    return float(np.sum(increments*cumulative[last]/(last+1))/labels.sum())


def run(source_root,run_path,output):
    run_path,output=Path(run_path),Path(output)
    output.mkdir(parents=True,exist_ok=False)
    report=json.loads((run_path/'report.json').read_text(encoding='utf8'))
    protocol=json.loads((run_path/'protocol.json').read_text(encoding='utf8'))
    if report['status']!='Gate_A2_A3_downstream_complete':raise ValueError('Completed experiment required')
    for path,value in protocol['code'].items():
        if digest(run_path/'frozen-code'/path)!=value:raise ValueError('Frozen experiment source changed')
    if digest(run_path/'frozen-policy.json')!=protocol['policy_sha256']:raise ValueError('Frozen policy changed')
    (X,y,meta),(VX,vy,vmeta)=load_data(source_root)[0]
    identities=hashlib.sha256('\n'.join(vmeta.transaction_id).encode()).hexdigest()
    expected=json.loads((run_path/'alignment.json').read_text(encoding='utf8'))
    if identities!=expected['development_ids_sha256']:raise ValueError('Prediction identities changed')
    records={};preprocessors={};reload_max=0.;coverage={}
    columns=[c for c in MODEL_COLUMNS if c not in ('mcc','channel')]
    raw=X[columns].to_numpy(dtype=np.float64);transformed=np.sign(raw)*np.log1p(np.abs(raw))
    median=np.nan_to_num(np.nanmedian(transformed,axis=0))
    q25,q75=np.nanpercentile(transformed,[25,75],axis=0);scale=np.maximum(np.nan_to_num(q75-q25),.25)
    sample=np.random.default_rng(187).choice(len(VX),96,replace=False)
    for name,value in report['results'].items():
        all_scores=[]
        for seed in value['seeds']:
            number=seed['config']['seed'];folder=run_path/name/f'seed-{number}'
            if len(seed['curves'])!=5 or any(c['rows']!=len(X) or c['positives']!=int(y.sum()) for c in seed['curves']):
                raise ValueError('Actual training budget/class count mismatch')
            for filename,sha in seed['artifacts'].items():
                if digest(folder/'model'/filename)!=sha:raise ValueError('Model artifact changed')
            scores=np.load(folder/'development-scores.npy',allow_pickle=False)
            if scores.shape!=vy.shape or not np.isfinite(scores).all():raise ValueError('Scores misaligned/nonfinite')
            predictor=load(folder/'model');replay=predictor.predict(VX.iloc[sample])
            difference=float(np.max(np.abs(replay-scores[sample])));reload_max=max(reload_max,difference)
            if difference>1e-6:raise ValueError('Independent reload mismatch')
            if not seed['config']['include_channel']:
                changed=VX.iloc[sample].copy();changed['channel']='Online Transaction'
                if not np.array_equal(predictor.predict(changed),replay):raise ValueError('No-channel condition reads channel')
                if predictor.prep.numeric.categories!=('mcc',) or any(c!='unknown' for _,c in predictor.prep.keys):
                    raise ValueError('No-channel preprocessor includes hidden channel information')
            prep=predictor.prep.numeric
            if prep.columns!=columns or not np.array_equal(prep.median,median) or not np.array_equal(prep.scale,scale):
                raise ValueError('Preprocessing differs from independently recomputed train-only statistics')
            for field,vocabulary in prep.vocabularies.items():
                expected_vocabulary={v:i+1 for i,v in enumerate(sorted(set(X[field].fillna('<MISSING>').astype(str))))}
                if vocabulary!=expected_vocabulary:raise ValueError('Vocabulary not fitted exclusively on training data')
            channel=seed['config']['include_channel']
            if channel not in coverage:
                keys=[predictor.prep.key(m,c) for m,c in zip(X.mcc,X.channel)]
                expected_keys=[('unknown','unknown')]+sorted(set(keys)-{('unknown','unknown')})
                if expected_keys!=predictor.prep.keys:raise ValueError('Auxiliary vocabulary includes nontraining categories')
                observed=set(keys)
                unknown=np.array([predictor.prep.key(m,c) not in observed for m,c in zip(VX.mcc,VX.channel)])
                coverage[channel]={'development_unseen_joint_keys':int(unknown.sum()),
                                   'development_unseen_joint_positive':int(vy[unknown].sum()),
                                   'development_rows':len(VX)}
            reference=preprocessors.setdefault(seed['config']['include_channel'],prep)
            if not np.array_equal(reference.median,prep.median) or not np.array_equal(reference.scale,prep.scale) or reference.vocabularies!=prep.vocabularies:
                raise ValueError('Compared models use different preprocessing')
            all_scores.append(scores)
        ensemble=np.mean(all_scores,axis=0)
        stored=np.load(run_path/name/'ensemble.npy',allow_pickle=False)
        if not np.array_equal(ensemble,stored):raise ValueError('Ensemble seed inclusion/order mismatch')
        ap=manual_ap(vy,ensemble)
        if abs(ap-value['ensemble']['ap'])>1e-12:raise ValueError('Manual threshold-tied AP mismatch')
        metrics,queue=evaluate_scores(vmeta,vy,ensemble)
        if metrics['daily_review']['true_positives']!=value['ensemble']['tp_daily10'] or len(queue)!=3650:
            raise ValueError('Independent heap daily ranking mismatch')
        records[name]={'model_reloads':len(all_scores),'seed_numbers':[s['config']['seed'] for s in value['seeds']],
                       'manual_AP':ap,'heap_TP':metrics['daily_review']['true_positives'],'reviewed':len(queue)}
    if not report['original_residual_reproduction_matches']:raise ValueError('Original residual not reproduced under matched matrix')
    result={'status':'Gate_A_matched_independently_verified','conditions':records,'total_models':sum(r['model_reloads'] for r in records.values()),
            'reload_max_abs_diff':reload_max,'original_residual_reproduction_max_abs_diff':report['original_residual_reproduction_max_abs_diff'],
            'report_sha256':digest(run_path/'report.json'),'frozen_source_verified':True,
            'training_only_preprocessing_matched':True,'nochannel_invariance_verified':True,'category_coverage':coverage,
            'final_2019_read':False,'paid_calls':0}
    write_json(output/'report.json',result);return result
