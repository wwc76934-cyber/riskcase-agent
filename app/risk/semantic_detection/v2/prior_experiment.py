"""Pre-registered low-label category-prior supplement, independent of D32."""
import hashlib
import math
from pathlib import Path
import shutil
import subprocess
import time
import traceback

import joblib
import numpy as np
import torch
from threadpoolctl import threadpool_limits

from app.risk.ml_evaluation import evaluate_scores
from app.risk.semantic_detection.common import digest,write_json
from app.risk.semantic_detection.data import load_data
from app.risk.semantic_detection.evaluation import Ranking
from .audit_history import block_interval
from .audit_matched import manual_ap
from .expanded_controls import predict
from .prior_fit import fit
from .prior_representations import build_tables
from .semi_experiment import read,load_student
from .semi_audit import independent_state_hash,independent_mask
from .semi_supervised import MaskedBroadModel
from .matched import Config
from app.risk.neural_model import seed_training


def validate(policy):
    if policy.get('implementation_ready') is not True or policy.get('status')!='registered_before_primary_D_development_scores':
        raise ValueError('Published registration and tested frozen implementation required')
    if policy.get('representations')!=['qwen_frozen32','random_frozen32','tfidf_frozen32'] or policy.get('seeds')!=[42,43,44]:
        raise ValueError('Complete fixed three-representation by three-seed grid required')
    if policy.get('dimensions')!=32 or policy.get('epochs')!=12 or policy.get('new_neural_instances')!=9:
        raise ValueError('No larger model, dimensions, or search budget admitted')
    if policy.get('registration_commit')!='a01e131b9d9a5b7ab029e74fcbb69adfea9b3ca6':
        raise ValueError('Public pre-evaluation registration identity required')
    if policy.get('mask_sha256')!='3172f1b650192444af31717b5b2b08d8b6ca6abd19c716c67e1843eba1720d4c':
        raise ValueError('Same frozen label visibility required')


def verify_parent(parent,audit):
    parent,audit=Path(parent),Path(audit)
    if not (parent/'report.json').is_file() or not (audit/'report.json').is_file():
        raise ValueError('Original D32 completed independent audit required before supplement fitting')
    result,proof=read(parent/'report.json'),read(audit/'report.json')
    if result['status']!='Gate_D_controlled_training_and_evaluation_complete' or proof['status']!='Gate_D_controlled_experiment_independently_verified' or proof['full_model_replays']!=32:
        raise ValueError('Original D32 must remain complete and unchanged')
    if proof['report_sha256']!=digest(parent/'report.json') or proof['protocol_sha256']!=digest(parent/'protocol.json'):
        raise ValueError('Parent D audit refers to different bytes')
    return {'parent_report_sha256':digest(parent/'report.json'),'parent_audit_sha256':digest(audit/'report.json'),
        'parent_protocol_sha256':digest(parent/'protocol.json')}


def run(source_root,parent,parent_audit,broad_run,bank,policy_path,output):
    policy=read(policy_path);validate(policy);parent_proof=verify_parent(parent,parent_audit)
    if Path(bank).resolve()!=Path(policy['bank']).resolve() or Path(parent).resolve()!=Path(policy['main_D_run']).resolve():
        raise ValueError('Registered source bank and parent run required')
    if digest(Path(bank)/'manifest.json')!=policy['bank_manifest_sha256']:
        raise ValueError('Original audited category-text manifest required')
    root=Path(__file__).resolve().parents[4]
    if subprocess.run(['git','status','--porcelain'],cwd=root,capture_output=True,text=True,check=True).stdout.strip():
        raise ValueError('Commit tested implementation before supplement fitting')
    commit=subprocess.run(['git','rev-parse','HEAD'],cwd=root,capture_output=True,text=True,check=True).stdout.strip()
    parent,broad_run,output=map(Path,(parent,broad_run,output));output.mkdir(parents=True,exist_ok=False)
    files=list(read(parent/'protocol.json')['source_code_sha256'])+[
        'app/risk/semantic_detection/v2/prior_representations.py','app/risk/semantic_detection/v2/prior_fit.py',
        'app/risk/semantic_detection/v2/prior_experiment.py','app/risk/semantic_detection/v2/semi_audit.py']
    hashes={name:digest(root/name) for name in files}
    for name in files:
        dest=output/'frozen-code'/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(root/name,dest)
    protocol={**policy,**parent_proof,'policy_sha256':digest(policy_path),'source_commit':commit,'source_code_sha256':hashes,
        'ordinary_reference_identity':'Original D bce, same masks/non-auxiliary cold values/12epochs; no refitting or selection',
        'new_model_evaluation_deferred_until_all9_fits':True,'default_detector_changed':False,'paid_calls':0}
    write_json(output/'protocol.json',protocol);protocol_sha=digest(output/'protocol.json')
    try:
        loaded,provenance=load_data(source_root,train_only=True);X,truth,meta=loaded[0]
        masked=np.load(parent/'visible-labels.npy',allow_pickle=False)
        if hashlib.sha256(masked.tobytes()).hexdigest()!=policy['mask_sha256'] or masked.shape!=(len(X),):
            raise ValueError('Source masked labels differ')
        parent_data=read(parent/'data.json')
        if provenance!=parent_data['source']:raise ValueError('Original D source database fingerprints differ')
        if hashlib.sha256('\n'.join(meta.transaction_id).encode()).hexdigest()!=parent_data['training_ids_sha256']:
            raise ValueError('Training row alignment differs')
        normalized=np.load(broad_run/'broad-normalized.npy',mmap_mode='r')
        if digest(broad_run/'broad-normalized.npy')!=parent_data['normalized_sha256']:
            raise ValueError('Fixed history feature bytes changed')
        prep_path=broad_run/'neural-seed-42/preprocessor.joblib'
        if digest(prep_path)!=parent_data['preprocessor_sha256']:raise ValueError('Training-X-only preparation differs')
        prep=joblib.load(prep_path);tables,table_info=build_tables(bank,prep,output/'tables')
        if table_info['text_sha256']!=policy['text_sha256']:raise ValueError('Audited allowed text changed')
        del truth,loaded
        records={};folders=[]
        for name in policy['representations']:
            condition={'name':name,'loss':'weighted_bce','consistency_weight':0.,'view_probability':0.}
            for seed in policy['seeds']:
                folder=output/name/f'seed-{seed}'
                _,record=fit(X,normalized[:len(X)],masked,prep,condition,seed,folder,protocol_sha256=protocol_sha,
                    representation_table=tables[name],representation_identity={'kind':name,'table_file_sha256':table_info['table_sha256'][name],
                        'source_text_sha256':table_info['text_sha256'],'LLM_encoder_run':False})
                baseline=read(parent/'bce'/f'seed-{seed}'/'training.json')
                if record['initialization']['ordinary_cold_state_sha256']!=baseline['initial_cold_state_sha256']:
                    raise ValueError('Original ordinary initialization does not match')
                records[f'{name}/seed-{seed}']=record;folders.append(folder)
        write_json(output/'all-training-complete.json',{'new_models':9,'model_sha256':{
            p.relative_to(output).as_posix():digest(p/'model.pt') for p in folders},'protocol_sha256':protocol_sha})
        loaded,full_provenance=load_data(source_root);(_,_,_),(VX,vy,vmeta)=loaded
        ranking=Ranking(vmeta,vy);scores={};evaluations={};torch.set_num_threads(2)
        if any(digest(root/name)!=sha for name,sha in hashes.items()):raise ValueError('Frozen supplement fitting code changed')
        for folder in folders:
            key=folder.relative_to(output).as_posix();model=load_student(folder/'model.pt',prep)
            started=time.monotonic();cpu=time.process_time()
            with threadpool_limits(limits=2):score=predict(model,prep,VX,normalized[len(X):])
            evaluation={'metrics':ranking.metrics(score,VX.channel.to_numpy()),'inference_wall_seconds':time.monotonic()-started,
                'inference_CPU_seconds':time.process_time()-cpu,'model_sha256':digest(folder/'model.pt')}
            np.save(folder/'development-scores.npy',score);write_json(folder/'evaluation.json',evaluation)
            scores[key]=score;evaluations[key]=evaluation
        ensembles={}
        for name in policy['representations']:
            score=np.mean([scores[f'{name}/seed-{seed}'] for seed in (42,43,44)],axis=0)
            np.save(output/name/'ensemble.npy',score);ensembles[name]=ranking.metrics(score,VX.channel.to_numpy())
        ordinary=np.load(parent/'bce/ensemble.npy',allow_pickle=False)
        ensembles['ordinary_learned32']=ranking.metrics(ordinary,VX.channel.to_numpy())
        result={'status':'low_label_prior_supplement_complete','new_neural_instances':9,'records':records,'evaluations':evaluations,
            'ensembles':ensembles,'tables':table_info,'parent_evidence':parent_proof,'protocol_sha256':protocol_sha,
            'data':full_provenance,'default_detector_changed':False,'independent_external_test':False,'final_2019_read':False,
            'limits':['Existing deterministic MCC descriptions, not new business facts','Retrospective synthetic label availability',
                      'Ordinary trainable table has more parameters; random/TFIDF frozen controls exactly match Qwen capacity'],
            'paid_calls':0}
        write_json(output/'report.json',result);return result
    except Exception as exc:
        write_json(output/'failure.json',{'type':type(exc).__name__,'message':str(exc),'traceback':traceback.format_exc()});raise


def audit(source_root,experiment_run,parent,parent_audit,broad_run,output):
    experiment_run,parent,broad_run,output=map(Path,(experiment_run,parent,broad_run,output));output.mkdir(parents=True,exist_ok=False)
    report,protocol=read(experiment_run/'report.json'),read(experiment_run/'protocol.json');validate(protocol)
    proof=verify_parent(parent,parent_audit)
    if proof!=report['parent_evidence'] or report['protocol_sha256']!=digest(experiment_run/'protocol.json'):
        raise ValueError('Supplement/parent protocol binding differs')
    root=Path(__file__).resolve().parents[4]
    for name,sha in protocol['source_code_sha256'].items():
        if digest(root/name)!=sha or digest(experiment_run/'frozen-code'/name)!=sha:raise ValueError('Frozen supplement source changed')
    loaded,provenance=load_data(source_root);(X,truth,meta),(VX,vy,vmeta)=loaded
    mask=independent_mask(meta,truth,read(parent/'protocol.json')['visibility'])
    if hashlib.sha256(mask.tobytes()).hexdigest()!=protocol['mask_sha256'] or not np.array_equal(mask,np.load(parent/'visible-labels.npy',allow_pickle=False)):
        raise ValueError('Independent supplement visibility reproduction differs')
    data=read(parent/'data.json')
    if any(provenance[key]!=data['source'][key] for key in ('manifest_sha256','features_sha256','labels_sha256')):
        raise ValueError('Source database provenance changed after parent verification')
    if hashlib.sha256('\n'.join(meta.transaction_id).encode()).hexdigest()!=data['training_ids_sha256'] or digest(broad_run/'broad-normalized.npy')!=data['normalized_sha256'] or digest(broad_run/'neural-seed-42/preprocessor.joblib')!=data['preprocessor_sha256']:
        raise ValueError('Supplement input/source alignment changed')
    normalized=np.load(broad_run/'broad-normalized.npy',mmap_mode='r');prep=joblib.load(broad_run/'neural-seed-42/preprocessor.joblib')
    tables={name:np.load(experiment_run/'tables'/(name+'.npy'),allow_pickle=False) for name in protocol['representations']}
    bank=Path(protocol['bank']);bank_manifest=read(bank/'manifest.json')
    spec=next(s for s in bank_manifest['specifications'] if s['name']=='nochannel-original')
    if digest(bank/'manifest.json')!=report['tables']['source_manifest_sha256'] or [tuple(k) for k in spec['keys']]!=prep.keys:
        raise ValueError('Audited source category texts/vocabulary changed')
    def unit(values):
        values=np.asarray(values,dtype=np.float32).copy();values/=np.maximum(np.linalg.norm(values,axis=1,keepdims=True),1e-12);values[0]=0.;return values
    if digest(bank/'nochannel-original.npy')!=protocol['bank_vectors_sha256']:raise ValueError('Source Qwen cache changed')
    np.testing.assert_array_equal(tables['qwen_frozen32'],unit(np.load(bank/'nochannel-original.npy',allow_pickle=False)[:,:32]))
    np.testing.assert_array_equal(tables['random_frozen32'],unit(np.random.default_rng(20261102).normal(size=(len(prep.keys),32))))
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.decomposition import TruncatedSVD
    lexical=TfidfVectorizer(lowercase=True,ngram_range=(1,2),sublinear_tf=True,dtype=np.float64).fit_transform(spec['texts'][1:])
    lexical=TruncatedSVD(n_components=32,random_state=20261102).fit_transform(lexical)
    np.testing.assert_allclose(tables['tfidf_frozen32'],unit(np.vstack([np.zeros((1,32)),lexical])),atol=1e-6,rtol=1e-6)
    marker=read(experiment_run/'all-training-complete.json');ranking=Ranking(vmeta,vy);scores={};replays={};maximum=0.;torch.set_num_threads(2)
    for name in protocol['representations']:
        values=[]
        if digest(experiment_run/'tables'/(name+'.npy'))!=report['tables']['table_sha256'][name]:raise ValueError('Fixed table changed')
        for seed in (42,43,44):
            key=f'{name}/seed-{seed}';folder=experiment_run/name/f'seed-{seed}';record=read(folder/'training.json')
            baseline=read(parent/'bce'/f'seed-{seed}'/'training.json');init=record['initialization']
            if init['ordinary_cold_state_sha256']!=baseline['initial_cold_state_sha256'] or not init['non_auxiliary_initial_values_matched']:
                raise ValueError('Downstream initialization differs')
            if init['trainable_parameters']!=init['total_parameters']-len(prep.keys)*32:
                raise ValueError('Frozen-table capacity mismatch')
            if record['model_sha256']!=digest(folder/'model.pt') or marker['model_sha256'][key]!=record['model_sha256']:
                raise ValueError('Saved model changed after training')
            if len(record['curves'])!=12 or record['last_epoch']!=12 or record['masked_label_vector_sha256']!=protocol['mask_sha256']:
                raise ValueError('Budget or visibility differs')
            seed_training(seed,2);cold=MaskedBroadModel(Config(representation='learned',include_channel=False,seed=seed,threads=2),prep,220)
            if independent_state_hash(cold)!=baseline['initial_cold_state_sha256']:raise ValueError('Ordinary cold state does not independently reproduce')
            with torch.no_grad():cold.point.aux.weight.copy_(torch.from_numpy(tables[name]))
            if independent_state_hash(cold)!=record['initial_cold_state_sha256']:
                raise ValueError('Actual frozen initial weights do not independently reproduce')
            if record['loss']['gamma']!=0 or record['loss']['raw_positive_coefficient']!=20 or record['loss']['raw_negative_coefficient']!=1 or record['loss']['fixed_loss_scale']!=1:
                raise ValueError('Actual fixed BCE20 loss differs')
            for row in record['curves']:
                if row['visible_positive_rows']!=91 or row['visible_negative_rows']!=82966 or row['rows']!=len(X) or row['unlabeled_rows']!=758835 or row['consistency_weight']:
                    raise ValueError('Actual optimizer exposure/consistency differs')
                expected_lr=.00005+(.001-.00005)*(1+math.cos(math.pi*(row['epoch']-1)/12))/2
                if abs(row['learning_rate']-expected_lr)>1e-12 or row['optimizer_steps']!=math.ceil(len(X)/4096):
                    raise ValueError('Actual learning rate/step budget differs')
            model=load_student(folder/'model.pt',prep)
            state=torch.load(folder/'model.pt',weights_only=True,map_location='cpu')
            if state['representation_identity']!=record['representation_identity'] or state['initialization']!=init or state['final_epoch']!=12 or state['condition']!=record['condition'] or state['masked_label_vector_sha256']!=protocol['mask_sha256'] or state['protocol_sha256']!=digest(experiment_run/'protocol.json') or state['initial_cold_state_sha256']!=record['initial_cold_state_sha256']:
                raise ValueError('Model identity/budget/visibility does not match frozen record')
            if record['condition']!={'name':name,'loss':'weighted_bce','consistency_weight':0.,'view_probability':0.}:
                raise ValueError('Supplement loss/representation condition differs')
            if model.point.aux.weight.requires_grad:raise ValueError('Auxiliary table is not frozen on saved model')
            np.testing.assert_array_equal(model.point.aux.weight.detach().numpy(),tables[name])
            with threadpool_limits(limits=2):replay=predict(model,prep,VX,normalized[len(X):])
            score=np.load(folder/'development-scores.npy',allow_pickle=False);difference=float(np.max(np.abs(replay-score)))
            if difference>1e-6:raise ValueError('Full supplement score replay differs')
            maximum=max(maximum,difference);metrics,_=evaluate_scores(vmeta,vy,score);ap=manual_ap(vy,score)
            if metrics['daily_review']['true_positives']!=report['evaluations'][key]['metrics']['tp_daily10'] or abs(ap-report['evaluations'][key]['metrics']['ap'])>1e-12:
                raise ValueError('Independent queue/AP differs')
            replays[key]={'rows':len(score),'max_abs_diff':difference,'manual_AP':ap};values.append(score)
            print(f'prior-audit {key} rows={len(score)} diff={difference:g}',flush=True)
        ensemble=np.mean(values,axis=0)
        if not np.array_equal(ensemble,np.load(experiment_run/name/'ensemble.npy')):raise ValueError('Supplement seed average differs')
        scores[name]=ensemble
    scores['ordinary_learned32']=np.load(parent/'bce/ensemble.npy',allow_pickle=False)
    comparisons={}
    for control in ('ordinary_learned32','random_frozen32','tfidf_frozen32'):
        value=ranking.compare(scores[control],scores['qwen_frozen32'])
        value['block_sensitivity']=block_interval(list(value['daily_net'].values()));comparisons['qwen_vs_'+control]=value
    result={'status':'low_label_prior_supplement_independently_verified','full_new_model_replays':9,'max_full_replay_abs_diff':maximum,
        'replays':replays,'comparisons':comparisons,'report_sha256':digest(experiment_run/'report.json'),
        'protocol_sha256':digest(experiment_run/'protocol.json'),'parent_evidence':proof,'default_detector_changed':False,
        'independent_external_test':False,'final_2019_read':False,'paid_calls':0}
    write_json(output/'report.json',result);return result
