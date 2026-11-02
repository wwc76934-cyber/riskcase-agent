"""Independent mask reconstruction, budgets, cold starts and full score replays."""
import hashlib
import math
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import torch
from threadpoolctl import threadpool_limits

from app.risk.ml_evaluation import evaluate_scores
from app.risk.neural_model import seed_training
from app.risk.semantic_detection.common import digest,write_json
from app.risk.semantic_detection.data import load_data
from app.risk.semantic_detection.evaluation import Ranking
from .audit_history import block_interval
from .audit_matched import manual_ap
from .expanded_controls import predict
from .matched import Config
from .semi_experiment import read,load_student,tree_frame,validate_policy
from .semi_supervised import MaskedBroadModel


def independent_mask(metadata,truth,definition):
    stamp=np.asarray(pd.to_datetime(metadata.event_time),dtype='datetime64[ns]')
    eligible=stamp+np.timedelta64(definition['delay_days'],'D')<=np.datetime64(definition['cutoff'])
    maximum=int(definition['fraction']*2**64);selected=[]
    for identity in metadata.transaction_id.astype(str):
        prefix=b'riskcase-v21-controlled-labels-v1\x00'+str(definition['seed']).encode()+b'\x00'
        number=int.from_bytes(hashlib.sha256(prefix+identity.encode()).digest()[0:8],byteorder='big')
        selected.append(number<maximum)
    visible=eligible&np.asarray(selected,dtype=bool)
    result=np.empty(len(truth),dtype=np.int8);result.fill(-1);result[visible]=truth[visible]
    return result


def independent_state_hash(model):
    h=hashlib.sha256()
    for name,value in sorted(model.state_dict().items()):
        tensor=value.detach().cpu().contiguous()
        h.update((name+'\0'+str(tensor.dtype)+'\0'+str(tuple(tensor.shape))+'\0').encode())
        h.update(tensor.numpy().tobytes())
    return h.hexdigest()


def expected_loss(name,negative,positive):
    frequency=np.array([negative,positive],dtype=float)/(negative+positive)
    base=float(frequency@np.array([1.,20.]))
    if name=='weighted_bce':a=np.array([1.,20.]);gamma=0.;scale=1.
    elif name=='standard_focal':a=np.array([.75,.25]);gamma=2.;scale=base/(float(frequency@a)/4)
    elif name=='matched_weight_focal':a=np.array([1/21,20/21]);gamma=2.;scale=84.
    elif name=='class_balanced_focal':
        a=np.array([.0001/(1-math.pow(.9999,negative)),.0001/(1-math.pow(.9999,positive))])
        a/=float(frequency@a);gamma=2.;scale=base*4
    else:raise ValueError('Unknown registered loss')
    return a,gamma,scale


def run(source_root,experiment_run,history_run,broad_run,output):
    experiment_run,history_run,broad_run,output=map(Path,(experiment_run,history_run,broad_run,output))
    output.mkdir(parents=True,exist_ok=False)
    report=read(experiment_run/'report.json');protocol=read(experiment_run/'protocol.json');validate_policy(protocol)
    if report['status']!='Gate_D_controlled_training_and_evaluation_complete' or report['protocol_sha256']!=digest(experiment_run/'protocol.json'):
        raise ValueError('Complete fixed D training required before audit')
    root=Path(__file__).resolve().parents[4]
    for path,sha in protocol['source_code_sha256'].items():
        if digest(root/path)!=sha or digest(experiment_run/'frozen-code'/path)!=sha:
            raise ValueError('Frozen D optimization implementation changed')
    loaded,provenance=load_data(source_root);(X,truth,meta),(VX,vy,vmeta)=loaded
    masked=independent_mask(meta,truth,protocol['visibility'])
    saved_mask=np.load(experiment_run/'visible-labels.npy',allow_pickle=False)
    if not np.array_equal(masked,saved_mask):raise ValueError('Independent delay/hash/unknown mask differs')
    mask_sha=hashlib.sha256(masked.tobytes()).hexdigest();visibility=read(experiment_run/'visibility.json')
    if mask_sha!=visibility['visible_label_vector_sha256']:raise ValueError('Visibility receipt differs')
    npositive=int((masked==1).sum());nnegative=int((masked==0).sum());nunknown=int((masked<0).sum())
    data=read(experiment_run/'data.json');features=read(broad_run/'features.json')
    for name,key in [('broad-normalized.npy','normalized_sha256'),('broad-raw.npy','raw_sha256')]:
        if digest(broad_run/name)!=data[key] or data[key]!=features[key]:raise ValueError('Shared feature artifact changed')
    prep_path=broad_run/'neural-seed-42/preprocessor.joblib'
    if digest(prep_path)!=data['preprocessor_sha256']:raise ValueError('Data-only preparation changed')
    prep=joblib.load(prep_path);normalized=np.load(broad_run/'broad-normalized.npy',mmap_mode='r')
    raw=np.load(broad_run/'broad-raw.npy',mmap_mode='r')
    ranking=Ranking(vmeta,vy);torch.set_num_threads(2);cold={}
    for seed in (42,43,44):
        seed_training(seed,2);model=MaskedBroadModel(Config(representation='learned',include_channel=False,seed=seed,threads=2),prep,220)
        cold[seed]=independent_state_hash(model)
    before_eval=read(experiment_run/'all-training-complete.json')
    if before_eval['main_instances']!=27 or before_eval['diagnostic_continuations']!=4 or before_eval['tree_instances']!=1:
        raise ValueError('Training completion accounting differs')
    folder_specs=[(experiment_run/c['name']/f'seed-{seed}',c,seed,1,12) for c in protocol['conditions'] for seed in protocol['seeds']]
    folder_specs += [(experiment_run/'diagnostics'/c['name'],c,42,13,18) for c in protocol['conditions'][:4]]
    if len(before_eval['model_sha256'])!=31:raise ValueError('Every final student must be fixed before evaluation')
    scores={};replays={};max_diff=0.
    for folder,condition,seed,first,last in folder_specs:
        key=folder.relative_to(experiment_run).as_posix();record=read(folder/'training.json');state=torch.load(folder/'model.pt',weights_only=True,map_location='cpu')
        if record['condition']!=condition or record['seed']!=seed or record['initial_cold_state_sha256']!=cold[seed]:
            raise ValueError('Condition/seed/cold start identity differs')
        if state['initial_cold_state_sha256']!=cold[seed] or state['masked_label_vector_sha256']!=mask_sha or state['protocol_sha256']!=digest(experiment_run/'protocol.json'):
            raise ValueError('Saved student does not match masked cold-start protocol')
        if state['condition']!=condition or state['seed']!=seed or state['final_epoch']!=last:
            raise ValueError('Saved final condition/seed/epoch metadata differs')
        if digest(folder/'model.pt')!=record['model_sha256'] or before_eval['model_sha256'][str(folder.relative_to(experiment_run))]!=record['model_sha256']:
            raise ValueError('Model bytes changed after training-complete marker')
        if record['first_epoch']!=first or record['last_epoch']!=last or len(record['curves'])!=last-first+1:
            raise ValueError('Actual fixed epoch budget differs')
        for row,epoch in zip(record['curves'],range(first,last+1)):
            lr=.00005+(.001-.00005)*(1+math.cos(math.pi*(epoch-1)/12))/2 if epoch<=12 else .00005
            weight=condition['consistency_weight']*min(1.,max(0.,(epoch-2)/3))
            if row['epoch']!=epoch or row['rows']!=len(X) or row['visible_positive_rows']!=npositive or row['visible_negative_rows']!=nnegative or row['unlabeled_rows']!=nunknown:
                raise ValueError('Actual class exposure differs')
            if row['optimizer_steps']!=math.ceil(len(X)/4096) or abs(row['learning_rate']-lr)>1e-12 or abs(row['consistency_weight']-weight)>1e-12:
                raise ValueError('Steps, schedule, or consistency warmup differs')
            if any(not math.isfinite(float(value)) for value in row.values() if isinstance(value,(int,float))):
                raise ValueError('Nonfinite convergence or gradient diagnostic')
        a,gamma,scale=expected_loss(condition['loss'],nnegative,npositive);loss=record['loss']
        np.testing.assert_allclose([loss['raw_negative_coefficient'],loss['raw_positive_coefficient']],a,rtol=1e-10,atol=1e-12)
        if loss['gamma']!=gamma or abs(loss['fixed_loss_scale']/scale-1)>1e-10 or loss['visible_positive']!=npositive or loss['visible_negative']!=nnegative:
            raise ValueError('Actual class weights or loss scale differ')
        if first==13:
            source=experiment_run/condition['name']/'seed-42/epoch-12.pt'
            if record['diagnostic_resume_sha256']!=digest(source):raise ValueError('Diagnosis did not continue its own unchanged low-label checkpoint')
        model=load_student(folder/'model.pt',prep)
        if sum(p.numel() for p in model.parameters())!=record['student_parameters'] or record['teacher_trainable_parameters']!=0:
            raise ValueError('Capacity/teacher-gradient restriction differs')
        with threadpool_limits(limits=2):replay=predict(model,prep,VX,normalized[len(X):])
        score=np.load(folder/'development-scores.npy',allow_pickle=False);difference=float(np.max(np.abs(replay-score)))
        if difference>1e-6:raise ValueError('Full masked NN score replay differs')
        max_diff=max(max_diff,difference);scores[key]=score
        metrics,queue=evaluate_scores(vmeta,vy,score);evaluation=read(folder/'evaluation.json');ap=manual_ap(vy,score)
        if metrics['daily_review']['true_positives']!=evaluation['metrics']['tp_daily10'] or abs(ap-evaluation['metrics']['ap'])>1e-12:
            raise ValueError('Independent per-seed daily queue/AP differs')
        replays[key]={'rows':len(score),'max_abs_diff':difference,'heap_TP':metrics['daily_review']['true_positives'],'manual_AP':ap}
        print(f'D-audit {key} rows={len(score)} diff={difference:g}',flush=True)
    ensembles={}
    for condition in protocol['conditions']:
        name=condition['name'];score=np.mean([scores[f'{name}/seed-{seed}'] for seed in (42,43,44)],axis=0)
        if not np.array_equal(score,np.load(experiment_run/name/'ensemble.npy',allow_pickle=False)):raise ValueError('Seed ensemble differs')
        ensembles[name]=score
        if ranking.metrics(score,VX.channel.to_numpy())!=report['ensembles'][name]:raise ValueError('Full ensemble metric receipt differs')
    tree=joblib.load(experiment_run/'masked-tree.joblib');model=tree.named_steps['model']
    if model.n_iter_!=100 or model.max_leaf_nodes!=15 or model.class_weight!='balanced':raise ValueError('Fixed visible-only tree budget differs')
    if int(model._predictors[0][0].nodes[0]['count'])!=npositive+nnegative:
        raise ValueError('Actual first-tree root saw a different training sample count')
    vocabulary=tree.named_steps['preprocess'].named_transformers_['mcc'].categories_[0]
    np.testing.assert_array_equal(vocabulary,np.unique(X.mcc.astype(str).to_numpy()[masked>=0]))
    AVX=tree_frame(VX,raw[len(X):],features['feature_names'],np.arange(len(VX)))
    with threadpool_limits(limits=2):tree_replay=tree.predict_proba(AVX)[:,1]
    tree_score=np.load(experiment_run/'masked-tree-development-scores.npy',allow_pickle=False)
    tree_diff=float(np.max(np.abs(tree_score-tree_replay)))
    if tree_diff>1e-12 or digest(experiment_run/'masked-tree.joblib')!=before_eval['tree_sha256']:raise ValueError('Visible-only tree changed')
    if ranking.metrics(tree_score,VX.channel.to_numpy())!=report['masked_tree']['metrics']:raise ValueError('Tree metric receipt differs')
    ensembles['masked_tree']=tree_score
    reference_files={'full_supervised_broad':broad_run/'neural-ensemble.npy','full_supervised_mean':history_run/'mean/ensemble.npy',
        'frozen_residual':Path(source_root)/'runs/neural-v2-development-01/ensemble-development-scores.npy'}
    for name,path in reference_files.items():ensembles[name]=np.load(path,allow_pickle=False)
    pairs=[(c['name'],'bce') for c in protocol['conditions'] if c['name']!='bce']
    pairs += [('focal_matched','focal'),('cbfocal','focal_matched'),('masked_tree','bce')]
    pairs += [(f'consistency_view_0{v}','bce_view') for v in (1,5)]
    pairs += [(f'consistency_view_0{v}',f'consistency_0{v}') for v in (1,5)]
    pairs += [(c['name'],ref) for c in protocol['conditions'] for ref in reference_files]
    comparisons={}
    for candidate,baseline in pairs:
        value=ranking.compare(ensembles[baseline],ensembles[candidate]);value['block_sensitivity']=block_interval(list(value['daily_net'].values()))
        comparisons[candidate+'_vs_'+baseline]=value
    diagnostics={}
    for condition in protocol['conditions'][:4]:
        name=condition['name'];old=scores[f'{name}/seed-42'];new=scores[f'diagnostics/{name}']
        diagnostics[name]={'epoch12':ranking.metrics(old,VX.channel.to_numpy()),'epoch18':ranking.metrics(new,VX.channel.to_numpy()),
            'paired_same_seed':ranking.compare(old,new),'used_to_replace_primary':False}
    mix=[]
    for year in (2016,2017):
        for channel in sorted(X.channel.unique()):
            group=meta.event_time.str.startswith(str(year)).to_numpy()&(X.channel.to_numpy()==channel)
            for tag,select in [('visible',masked>=0),('unlabeled',masked<0)]:
                selected=group&select;mix.append({'year':year,'channel':channel,'label_view':tag,'rows':int(selected.sum()),
                    'simulator_positive_after_fit_audit_only':int(truth[selected].sum())})
    result={'status':'Gate_D_controlled_experiment_independently_verified','full_model_replays':32,
        'neural_replays':31,'tree_replays':1,'rows_per_replay':len(VX),'max_full_NN_replay_abs_diff':max_diff,'tree_replay_abs_diff':tree_diff,
        'replays':replays,'mask_sha256':mask_sha,'visible_positive':npositive,'visible_negative':nnegative,'unlabeled':nunknown,
        'hidden_positive_simulator_audit_only':int(truth[masked<0].sum()),'year_channel_label_mix':mix,
        'comparisons':comparisons,'diagnostics':diagnostics,'cold_initial_state_by_seed':cold,
        'report_sha256':digest(experiment_run/'report.json'),'protocol_sha256':digest(experiment_run/'protocol.json'),
        'reference_score_sha256':{name:digest(path) for name,path in reference_files.items()},'data':provenance,
        'inherited_causal_features':'B independently rebuilt broad220 and strict user-card neighborhoods; exact reviewed bytes reused, not re-described as a second full feature reconstruction',
        'independent_external_test':False,'default_detector_changed':False,'final_2019_read':False,'paid_calls':0}
    write_json(output/'report.json',result);return result
