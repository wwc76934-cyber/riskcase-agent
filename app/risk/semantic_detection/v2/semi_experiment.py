"""Gated controlled retrospective study on existing private synthetic artifacts.

No new-data admission exception is granted here. IBM reuse retains its recorded
private-research restrictions; this is not data commercial/redistribution clearance.
"""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time
import traceback

import joblib
import numpy as np
import pandas as pd
import torch
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OrdinalEncoder
from threadpoolctl import threadpool_limits

from app.risk.semantic_detection.common import digest,write_json
from app.risk.semantic_detection.data import load_data
from app.risk.semantic_detection.evaluation import Ranking
from .expanded_controls import predict
from .matched import Config
from .semi_fit import fit,state_fingerprint
from .semi_supervised import MaskedBroadModel,controlled_visibility


def conditions():
    return [
        {'name':'bce','loss':'weighted_bce','consistency_weight':0.,'view_probability':0.},
        {'name':'focal','loss':'standard_focal','consistency_weight':0.,'view_probability':0.},
        {'name':'focal_matched','loss':'matched_weight_focal','consistency_weight':0.,'view_probability':0.},
        {'name':'cbfocal','loss':'class_balanced_focal','consistency_weight':0.,'view_probability':0.},
        {'name':'bce_view','loss':'weighted_bce','consistency_weight':0.,'view_probability':.1},
        {'name':'consistency_01','loss':'weighted_bce','consistency_weight':.1,'view_probability':0.},
        {'name':'consistency_05','loss':'weighted_bce','consistency_weight':.5,'view_probability':0.},
        {'name':'consistency_view_01','loss':'weighted_bce','consistency_weight':.1,'view_probability':.1},
        {'name':'consistency_view_05','loss':'weighted_bce','consistency_weight':.5,'view_probability':.1}]


def read(path):return json.loads(Path(path).read_text(encoding='utf8'))


def verify_B(report_path,review_path):
    if not Path(report_path).is_file() or not Path(review_path).is_file():
        raise ValueError('Gate B full independent verification and technical/visual review required first')
    report,review=read(report_path),read(review_path)
    if report.get('status')!='Gate_B_complete_and_independently_verified' or report.get('full_model_replays')!=43:
        raise ValueError('All43 B model replays must have passed')
    if review.get('status')!='Gate_B_technical_and_visual_review_complete' or review.get('history_results_sha256')!=digest(report_path):
        raise ValueError('Gate B review does not bind this completed result')
    if report.get('default_detector_changed') is not False or report.get('final_2019_read') is not False:
        raise ValueError('Default/split restrictions changed')
    tests=report.get('tests',{})
    if not tests.get('tests') or tests.get('failures')!=0 or tests.get('errors')!=0:
        raise ValueError('Passing B regression evidence required')
    if len(report.get('source_reports',{}))!=5:raise ValueError('Five B source receipts required')
    for path,sha in report['source_reports'].items():
        if digest(path)!=sha:raise ValueError('B source receipt changed: '+path)
    if len(review.get('reuse_artifacts_sha256',{}))!=4:raise ValueError('Four label-free feature/preparation artifacts must be bound by B review')
    for path,sha in review['reuse_artifacts_sha256'].items():
        if digest(path)!=sha:raise ValueError('Reviewed reusable artifact changed: '+path)
    return {'history_results_sha256':digest(report_path),'review_sha256':digest(review_path),
            'source_reports':report['source_reports']}


def validate_policy(policy):
    if policy.get('status')!='frozen_after_B_verified' or policy.get('execution_allowed') is not True:
        raise ValueError('Conditional draft cannot start a D experiment')
    if policy.get('conditions')!=conditions() or policy.get('seeds')!=[42,43,44] or policy.get('main_epochs')!=12:
        raise ValueError('Complete fixed nine-condition / three-seed / twelve-epoch grid required')
    if policy.get('diagnostic_conditions')!=['bce','focal','focal_matched','cbfocal'] or policy.get('diagnostic_seed')!=42 or policy.get('diagnostic_extra_epochs')!=6:
        raise ValueError('Single bounded four-loss continuation diagnosis required')
    if policy.get('source_archive_sha256')!='e9f589a0958f40d60f81b1a2e8428db86e00c05755caf44fb055827976c0efa2':
        raise ValueError('Only the existing project synthetic snapshot is in scope')
    if policy.get('simulation_acknowledged') is not True or policy.get('evaluation_scope')!='previously_observed_2018_development':
        raise ValueError('Explicit simulated availability and observed-development scope required')
    if policy.get('source_usage_scope')!='existing_private_synthetic_research_no_raw_redistribution' or policy.get('pretrained_semantic_branch_admitted') is not False:
        raise ValueError('No new-source, commercial, release, or pretrained semantic admission')
    expected_mask={'fraction':.1,'seed':20261102,'delay_days':7,'cutoff':'2017-12-31T23:59:59'}
    if policy.get('visibility')!=expected_mask:raise ValueError('Frozen label visibility definition required')
    if [policy.get(key) for key in ('main_neural_instances','diagnostic_continuations','masked_tree_instances','expected_full_replays')]!=[27,4,1,32]:
        raise ValueError('Explicit finite model accounting required')


def load_student(path,prep):
    state=torch.load(path,weights_only=True,map_location='cpu')
    model=MaskedBroadModel(Config(**state['config']),prep,state['dimensions'])
    model.load_state_dict(state['state_dict']);return model


def tree_frame(frame,raw,names,positions):
    current=[c for c in frame if c not in ('mcc','channel')]
    result=pd.concat([frame.iloc[positions][current].astype(np.float32).reset_index(drop=True),
        pd.DataFrame(np.asarray(raw[positions],dtype=np.float32),columns=names)],axis=1)
    result['mcc']=frame.iloc[positions].mcc.astype(str).to_numpy();return result


def run(source_root,history_run,broad_run,B_result,B_review,policy_path,output):
    # Guards run BEFORE dataset loading, masking, or creation of a training folder.
    policy=read(policy_path);validate_policy(policy);B=verify_B(B_result,B_review)
    if policy.get('B_review_sha256')!=digest(B_review):raise ValueError('Protocol frozen against a different B review')
    root=Path(__file__).resolve().parents[4]
    clean=subprocess.run(['git','status','--porcelain'],cwd=root,capture_output=True,text=True,check=True).stdout.strip()
    if clean:raise ValueError('Commit the reviewed source/protocol before real-data execution')
    commit=subprocess.run(['git','rev-parse','HEAD'],cwd=root,capture_output=True,text=True,check=True).stdout.strip()
    history_run,broad_run,output=map(Path,(history_run,broad_run,output))
    reviewed=read(B_review)['reuse_artifacts_sha256']
    for name in ('features.json','broad-normalized.npy','broad-raw.npy','neural-seed-42/preprocessor.joblib'):
        path=broad_run/name
        if reviewed.get(path.as_posix())!=digest(path):raise ValueError('B review does not authorize this exact reusable feature artifact')
    if shutil.disk_usage(root).free<1024**3:raise ValueError('One GiB free local evidence budget required')
    output.mkdir(parents=True,exist_ok=False)
    source_files=list(read(history_run/'protocol.json')['code'])+[
        'app/risk/semantic_detection/v2/expanded_controls.py','app/risk/semantic_detection/v2/semi_supervised.py',
        'app/risk/semantic_detection/v2/semi_fit.py','app/risk/semantic_detection/v2/semi_experiment.py']
    hashes={p:digest(root/p) for p in source_files}
    for name in source_files:
        destination=output/'frozen-code'/name;destination.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(root/name,destination)
    protocol={**policy,'source_commit':commit,'policy_sha256':digest(policy_path),'source_code_sha256':hashes,
        'B_evidence':B,'no_development_load_until_all_training_finishes':True,'default_detector_changed':False,
        'main_neural_instances':27,'diagnostic_continuations':4,'masked_tree_instances':1,'expected_full_replays':32,'paid_calls':0}
    write_json(output/'protocol.json',protocol);protocol_sha=digest(output/'protocol.json')
    try:
        training,provenance=load_data(source_root,train_only=True)
        X,y,meta=training[0]
        source_data=read(history_run/'data.json')
        if any(provenance[k]!=source_data[k] for k in ('manifest_sha256','features_sha256','labels_sha256')):
            raise ValueError('Existing source fingerprint differs from verified B')
        ids_sha=hashlib.sha256('\n'.join(meta.transaction_id).encode()).hexdigest()
        if ids_sha!=read(history_run/'graph.json')['training']['node_ids_sha256']:
            raise ValueError('Training array order differs from causal B features')
        feature_info=read(broad_run/'features.json')
        normalized=np.load(broad_run/'broad-normalized.npy',mmap_mode='r')
        raw=np.load(broad_run/'broad-raw.npy',mmap_mode='r')
        if feature_info['training_rows']!=len(X) or normalized.shape!=raw.shape or normalized.shape[1]!=220:
            raise ValueError('Verified broad220 training prefix required')
        if digest(broad_run/'broad-normalized.npy')!=feature_info['normalized_sha256'] or digest(broad_run/'broad-raw.npy')!=feature_info['raw_sha256']:
            raise ValueError('Label-free causal feature bytes changed')
        prep_path=broad_run/'neural-seed-42/preprocessor.joblib';prep=joblib.load(prep_path)
        masked,visibility=controlled_visibility(meta,y,simulation_acknowledged=True,**policy['visibility'])
        np.save(output/'visible-labels.npy',masked);write_json(output/'visibility.json',visibility)
        write_json(output/'data.json',{'source':provenance,'training_ids_sha256':ids_sha,
            'feature_info_sha256':digest(broad_run/'features.json'),'normalized_sha256':feature_info['normalized_sha256'],
            'raw_sha256':feature_info['raw_sha256'],'preprocessor_sha256':digest(prep_path),
            'no_neural_weights_reused':True,'preprocessing_scope':'All and only training X, including unlabeled training X, permitted; no hidden labels'})
        # Ground truth never enters the training worker or a model constructor.
        del y,training
        records={};folders=[]
        for condition in policy['conditions']:
            for seed in policy['seeds']:
                folder=output/condition['name']/f'seed-{seed}'
                _,record=fit(X,normalized[:len(X)],masked,prep,condition,seed,folder,protocol_sha256=protocol_sha)
                records[f"{condition['name']}/seed-{seed}"]=record;folders.append(folder)
        for condition in policy['conditions'][:4]:
            folder=output/'diagnostics'/condition['name']
            checkpoint=output/condition['name']/'seed-42/epoch-12.pt'
            previous=torch.load(checkpoint,weights_only=True,map_location='cpu')
            final=torch.load(checkpoint.parent/'model.pt',weights_only=True,map_location='cpu')
            if state_fingerprint(previous['state_dict'])!=state_fingerprint(final['state_dict']):
                raise ValueError('Diagnostic source differs from its own fixed twelve-epoch student')
            _,record=fit(X,normalized[:len(X)],masked,prep,condition,42,folder,protocol_sha256=protocol_sha,resume_checkpoint=checkpoint)
            records['diagnostic/'+condition['name']]=record;folders.append(folder)
        # A strong fixed supervised business control, using only visible targets.
        known=np.flatnonzero(masked>=0);current=[c for c in X if c not in ('mcc','channel')]
        names=feature_info['feature_names'];AX=tree_frame(X,raw[:len(X)],names,known)
        transformer=ColumnTransformer([('numeric','passthrough',current+names),
            ('mcc',OrdinalEncoder(dtype=np.float32,handle_unknown='use_encoded_value',unknown_value=-1),['mcc'])])
        tree=Pipeline([('preprocess',transformer),('model',HistGradientBoostingClassifier(max_iter=100,max_leaf_nodes=15,
            learning_rate=.1,min_samples_leaf=50,l2_regularization=1.,early_stopping=False,class_weight='balanced',random_state=42,
            categorical_features=[False]*(len(current)+220)+[True]))])
        tree_wall=time.monotonic();tree_cpu=time.process_time()
        with threadpool_limits(limits=2):tree.fit(AX,masked[known])
        tree_time={'training_wall_seconds':time.monotonic()-tree_wall,'training_CPU_seconds':time.process_time()-tree_cpu,
            'visible_rows':len(known),'visible_positive':int((masked[known]==1).sum()),'class_weight':'balanced visible classes only'}
        joblib.dump(tree,output/'masked-tree.joblib',compress=3);del AX
        write_json(output/'all-training-complete.json',{'status':'all_fixed_training_finished_before_development_load',
            'main_instances':27,'diagnostic_continuations':4,'tree_instances':1,'model_sha256':{str(p.relative_to(output)):digest(p/'model.pt') for p in folders},
            'tree_sha256':digest(output/'masked-tree.joblib'),'protocol_sha256':protocol_sha})
        # Previously observed development is now loaded for fixed, post-training evaluation only.
        full,full_provenance=load_data(source_root)
        (TX,truth,tm),(VX,vy,vmeta)=full
        if not TX.equals(X) or not tm.transaction_id.equals(meta.transaction_id):raise ValueError('Source changed during training')
        if any(digest(root/p)!=sha for p,sha in hashes.items()):raise ValueError('Frozen training implementation changed')
        ranking=Ranking(vmeta,vy);scores={};evaluations={};torch.set_num_threads(2)
        for folder in folders:
            model=load_student(folder/'model.pt',prep);start=time.monotonic();cpu=time.process_time()
            with threadpool_limits(limits=2):score=predict(model,prep,VX,normalized[len(X):])
            record={'metrics':ranking.metrics(score,VX.channel.to_numpy()),'inference_wall_seconds':time.monotonic()-start,
                'inference_CPU_seconds':time.process_time()-cpu,'model_sha256':digest(folder/'model.pt')}
            np.save(folder/'development-scores.npy',score);write_json(folder/'evaluation.json',record)
            key=str(folder.relative_to(output)).replace('\\','/');scores[key]=score;evaluations[key]=record
        ensembles={}
        for condition in policy['conditions']:
            name=condition['name'];score=np.mean([scores[f'{name}/seed-{seed}'] for seed in policy['seeds']],axis=0)
            np.save(output/name/'ensemble.npy',score);ensembles[name]=ranking.metrics(score,VX.channel.to_numpy())
        AVX=tree_frame(VX,raw[len(X):],names,np.arange(len(VX)));start=time.monotonic();cpu=time.process_time()
        with threadpool_limits(limits=2):tree_scores=tree.predict_proba(AVX)[:,1]
        tree_time.update({'inference_wall_seconds':time.monotonic()-start,'inference_CPU_seconds':time.process_time()-cpu,
            'metrics':ranking.metrics(tree_scores,VX.channel.to_numpy())})
        np.save(output/'masked-tree-development-scores.npy',tree_scores)
        result={'status':'Gate_D_controlled_training_and_evaluation_complete','protocol_sha256':protocol_sha,
            'visibility':visibility,'ensembles':ensembles,'seed_evaluations':evaluations,'training_records':records,'masked_tree':tree_time,
            'controlled_hidden_ground_truth_audit_only':{'unknown_rows':int((masked<0).sum()),'unknown_positive':int(truth[masked<0].sum()),
                'note':'Known simulator truth inspected by host after all parameter optimization, never interpreted as all-normal'},
            'data':full_provenance,'evaluation_opened_after_all_training':True,'independent_external_test':False,
            'pretrained_semantic_branch_admitted':False,'real_label_discovery_timestamps_verified':False,
            'data_commercial_or_redistribution_scope_verified':False,'default_detector_changed':False,'final_2019_read':False,'paid_calls':0}
        write_json(output/'report.json',result);return result
    except Exception as exc:
        write_json(output/'failure.json',{'type':type(exc).__name__,'message':str(exc),'traceback':traceback.format_exc()});raise
