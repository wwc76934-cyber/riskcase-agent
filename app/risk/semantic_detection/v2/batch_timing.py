"""Read-only, one-pass scoring timing: exclude metrics and checkpoint loading."""
from pathlib import Path
import time

import joblib
import numpy as np
import pandas as pd
import torch
from threadpoolctl import threadpool_limits

from app.risk.semantic_detection.common import digest,peak_working_set_bytes,write_json
from app.risk.semantic_detection.data import load_data
from .expanded_controls import predict
from .semi_experiment import load_student,read,tree_frame


def run(source_root,experiment_run,audit_run,broad_run,output):
    started=time.monotonic();cpu=time.process_time()
    experiment_run,audit_run,broad_run,output=map(Path,(experiment_run,audit_run,broad_run,output))
    result,audit=read(experiment_run/'report.json'),read(audit_run/'report.json')
    if audit['report_sha256']!=digest(experiment_run/'report.json'):raise ValueError('Bound full replay required')
    if result['status']=='Gate_D_controlled_training_and_evaluation_complete':
        if audit['status']!='Gate_D_controlled_experiment_independently_verified' or audit['full_model_replays']!=32:raise ValueError('D32 audit required')
        records=result['training_records'];kind='original_D32'
    elif result['status']=='low_label_prior_supplement_complete':
        if audit['status']!='low_label_prior_supplement_independently_verified' or audit['full_new_model_replays']!=9:raise ValueError('Supplement9 audit required')
        records=result['records'];kind='supplement9'
    else:raise ValueError('Only already-audited completed runs admitted')
    output.mkdir(parents=True,exist_ok=False)
    loaded,provenance=load_data(source_root);(X,_,_),(VX,_,_)=loaded
    if any(provenance[k]!=result['data'][k] for k in ('manifest_sha256','features_sha256','labels_sha256')):raise ValueError('Source changed')
    prep=joblib.load(broad_run/'neural-seed-42/preprocessor.joblib')
    normalized=np.load(broad_run/'broad-normalized.npy',mmap_mode='r');torch.set_num_threads(2)
    rows=[]
    for key,record in records.items():
        # Original coordinator records diagnostic/* but persists diagnostics/*.
        if key.startswith('diagnostic/'):key='diagnostics/'+key.split('/',1)[1]
        folder=experiment_run/key;model=load_student(folder/'model.pt',prep)
        if digest(folder/'model.pt')!=record['model_sha256']:raise ValueError('Audited weights changed')
        with threadpool_limits(limits=2):
            tick=time.monotonic();tick_cpu=time.process_time();score=predict(model,prep,VX,normalized[len(X):])
            seconds=time.monotonic()-tick;CPU=time.process_time()-tick_cpu
        difference=float(np.max(np.abs(score-np.load(folder/'development-scores.npy',allow_pickle=False))))
        if difference>1e-6:raise ValueError('Timed full score replay differs')
        rows.append({'model':key,'rows':len(VX),'scoring_wall_seconds':seconds,'scoring_CPU_seconds':CPU,
            'max_abs_diff':difference,'weights_sha256':record['model_sha256']})
        print(f'batch-timing {kind} {key} rows={len(VX)} seconds={seconds:.3f} diff={difference:g}',flush=True)
    if kind=='original_D32':
        tree=joblib.load(experiment_run/'masked-tree.joblib');raw=np.load(broad_run/'broad-raw.npy',mmap_mode='r')
        names=read(broad_run/'features.json')['feature_names']
        with threadpool_limits(limits=2):
            tick=time.monotonic();tick_cpu=time.process_time()
            AX=tree_frame(VX,raw[len(X):],names,np.arange(len(VX)));score=tree.predict_proba(AX)[:,1]
            seconds=time.monotonic()-tick;CPU=time.process_time()-tick_cpu
        difference=float(np.max(np.abs(score-np.load(experiment_run/'masked-tree-development-scores.npy',allow_pickle=False))))
        if difference>1e-6:raise ValueError('Timed tree replay differs')
        rows.append({'model':'masked_tree','rows':len(VX),'scoring_wall_seconds':seconds,'scoring_CPU_seconds':CPU,
            'max_abs_diff':difference,'weights_sha256':digest(experiment_run/'masked-tree.joblib')})
    pd.DataFrame(rows).to_csv(output/'batch-scoring.csv',index=False)
    report={'status':'audited_batch_scoring_timing_complete','kind':kind,'rows':len(VX),'models':rows,
        'max_abs_diff':max(r['max_abs_diff'] for r in rows),'source_report_sha256':digest(experiment_run/'report.json'),
        'audit_sha256':digest(audit_run/'report.json'),'code_sha256':digest(__file__),
        'timing_scope':'One CPU2-thread pass per already-loaded checkpoint; includes model input transformation and prediction; excludes data loading, checkpoint load, AP/queue metrics and replay comparison. Features already constructed; no repeated trial selection, online p99, or isolated-host claim.',
        'process_CPU_seconds_all_threads':time.process_time()-cpu,'whole_process_peak_bytes':peak_working_set_bytes(),
        'whole_process_wall_seconds':time.monotonic()-started,'new_training':False,'paid_calls':0,'default_detector_changed':False}
    write_json(output/'report.json',report);return report
