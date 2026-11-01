"""Gate-B fully supervised runs with strict identity/time scope and matched controls."""
from dataclasses import asdict
import json
from pathlib import Path
import shutil
import subprocess
import time
import platform

import joblib
import numpy as np
import pandas as pd
import torch
from threadpoolctl import threadpool_limits

from app.risk.neural_model import seed_training
from app.risk.semantic_detection.common import digest,fingerprint,peak_working_set_bytes,write_json
from app.risk.semantic_detection.evaluation import Ranking
from app.risk.semantic_detection.history_experiment import gather_history
from app.risk.semantic_detection.semantics import UNKNOWN,category_key
from app.risk.semantic_detection.temporal import historical_neighbors
from .history import Config,Model,StatsScaler,STAT_NAMES,deterministic_statistics
from .history_data import load_with_cards,verify_neighbors
from .matched import Preprocessor


class Predictor:
    def __init__(self,config,prep,model,scaler=None):
        self.config,self.prep,self.model,self.scaler=config,prep,model,scaler

    def predict(self,frame,targets,*,neighborhood=None,statistics=None,batch_size=2048):
        targets=np.asarray(targets,dtype=np.int64)
        needs_history=self.config.mode not in {'flat','statistics'} and not self.config.self_control
        if needs_history:
            tensors=self.prep.transform(frame)
        else:
            frame=frame.iloc[targets]
            if statistics is not None:statistics=statistics[targets]
            tensors=self.prep.transform(frame);targets=np.arange(len(targets))
        self.model.eval();scores=[]
        stat=self.scaler.transform(statistics) if self.config.mode=='statistics' else None
        with torch.inference_mode():
            for start in range(0,len(targets),batch_size):
                positions=targets[start:start+batch_size];kwargs={}
                if self.config.mode=='statistics':kwargs['statistics']=stat[positions]
                elif self.config.mode!='flat' and not self.config.self_control:
                    if neighborhood is None:raise ValueError('Explicit causal inference neighborhood required')
                    kwargs['history']=gather_history(tensors,*neighborhood,positions)
                logits=self.model(*(v[positions] for v in tensors),**kwargs)
                if not torch.isfinite(logits).all():raise ValueError('Nonfinite history inference')
                scores.append(logits.sigmoid().numpy())
        return np.concatenate(scores)

    def save(self,directory):
        directory=Path(directory);directory.mkdir(parents=True,exist_ok=False)
        joblib.dump({'preprocessor':self.prep,'statistics_scaler':self.scaler},directory/'preprocessor.joblib')
        torch.save({'config':asdict(self.config),'table_shape':self.model.point.table_shape,
                    'numeric_width':len(self.prep.numeric.columns)*2,'categorical_sizes':self.prep.numeric.categorical_sizes,
                    'state_dict':self.model.state_dict()},directory/'model.pt')


def load(directory):
    directory=Path(directory)
    state=torch.load(directory/'model.pt',map_location='cpu',weights_only=True)
    config=Config(**state['config'])
    model=Model(config,np.zeros(state['table_shape'],dtype=np.float32),state['numeric_width'],state['categorical_sizes'])
    model.load_state_dict(state['state_dict'])
    prep=joblib.load(directory/'preprocessor.joblib')
    return Predictor(config,prep['preprocessor'],model,prep['statistics_scaler'])


def fit(config,epochs,X,y,keys,neighborhood,context_frame,target_positions,inference_neighborhood,ranking,output,
        *,training_statistics=None,inference_statistics=None):
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    if epochs not in (12,) or set(np.unique(y))!={0,1} or len(X)!=len(y):
        raise ValueError('Fixed twelve-epoch fully supervised Gate B budget required')
    seed_training(config.seed,config.threads)
    prep=Preprocessor(config.point_config()).fit(X,keys);tensors=prep.transform(X)
    table=np.zeros((len(keys),32),dtype=np.float32)
    model=Model(config,table,tensors[0].shape[1],prep.numeric.categorical_sizes)
    scaler=StatsScaler().fit(training_statistics) if config.mode=='statistics' else None
    stat=scaler.transform(training_statistics) if scaler is not None else None
    predictor=Predictor(config,prep,model,scaler)
    target=torch.from_numpy(y.astype(np.float32))
    optimizer=torch.optim.AdamW(model.parameters(),lr=config.learning_rate,weight_decay=config.weight_decay)
    scheduler=torch.optim.lr_scheduler.CosineAnnealingLR(optimizer,T_max=epochs,eta_min=5e-5)
    generator=torch.Generator().manual_seed(config.seed);curves=[];started=time.monotonic()
    for epoch in range(1,epochs+1):
        model.train();order=torch.randperm(len(X),generator=generator);total=0.;positive_loss=0.;negative_loss=0.;max_norm=0.
        positive_gradient=0.;negative_gradient=0.;epoch_start=time.monotonic();learning_rate=optimizer.param_groups[0]['lr']
        for start in range(0,len(X),config.batch_size):
            positions=order[start:start+config.batch_size];kwargs={}
            if config.mode=='statistics':kwargs['statistics']=stat[positions]
            elif config.mode!='flat' and not config.self_control:
                kwargs['history']=gather_history(tensors,*neighborhood,positions.numpy())
            optimizer.zero_grad(set_to_none=True)
            logits=model(*(t[positions] for t in tensors),**kwargs);labels=target[positions]
            losses=torch.nn.functional.binary_cross_entropy_with_logits(logits,labels,
                    pos_weight=torch.tensor(config.positive_weight),reduction='none')
            loss=losses.mean()
            if not torch.isfinite(loss):raise ValueError('Nonfinite historical training loss')
            loss.backward();norm=torch.nn.utils.clip_grad_norm_(model.parameters(),5.,error_if_nonfinite=True)
            max_norm=max(max_norm,float(norm));optimizer.step();total+=float(loss.detach())*len(positions)
            positive=labels==1;negative=labels==0
            positive_loss+=float(losses.detach()[positive].sum());negative_loss+=float(losses.detach()[negative].sum())
            probability=logits.detach().sigmoid()
            positive_gradient+=float(((1-probability[positive])*config.positive_weight).sum())
            negative_gradient+=float(probability[negative].sum())
        row={'epoch':epoch,'loss':total/len(X),'positive_weighted_loss_mean':positive_loss/int(y.sum()),
             'negative_loss_mean':negative_loss/int((y==0).sum()),'positive_abs_logit_gradient_mean':positive_gradient/int(y.sum()),
             'negative_abs_logit_gradient_mean':negative_gradient/int((y==0).sum()),'rows':len(X),'positive_rows':int(y.sum()),
             'learning_rate':learning_rate,'max_preclip_gradient_norm':max_norm,
             'epoch_seconds':time.monotonic()-epoch_start,'elapsed_seconds':time.monotonic()-started}
        curves.append(row);write_json(output/'curves.json',curves);scheduler.step()
        torch.save({'epoch':epoch,'model':model.state_dict(),'optimizer':optimizer.state_dict(),'scheduler':scheduler.state_dict(),
                    'shuffle_generator':generator.get_state(),'torch_rng':torch.get_rng_state()},output/f'epoch-{epoch:02d}.pt')
        print(f'v21-B {config.mode} self{config.self_control} K{config.k} seed{config.seed} epoch{epoch} loss={row["loss"]:.6f} lr={learning_rate:.6g} seconds={row["elapsed_seconds"]:.1f}',flush=True)
    training_seconds=time.monotonic()-started;predictor.save(output/'model')
    begin=time.monotonic()
    scores=predictor.predict(context_frame,target_positions,neighborhood=inference_neighborhood,statistics=inference_statistics)
    inference_seconds=time.monotonic()-begin
    sample=np.random.default_rng(997).choice(len(target_positions),min(32,len(target_positions)),replace=False)
    replay=load(output/'model').predict(context_frame,np.asarray(target_positions)[sample],
                                        neighborhood=inference_neighborhood,statistics=inference_statistics)
    difference=float(np.max(np.abs(replay-scores[sample])))
    if difference>1e-6:raise ValueError('History reload differs')
    np.save(output/'development-scores.npy',scores)
    result={'config':asdict(config),'epochs':epochs,'curves':curves,'training_seconds':training_seconds,
            'prediction_seconds':inference_seconds,'prediction_rows':len(scores),'reload_max_abs_diff':difference,
            'parameters':sum(p.numel() for p in model.parameters()),
            'trainable_parameters':sum(p.numel() for p in model.parameters() if p.requires_grad),
            'whole_process_peak_bytes':peak_working_set_bytes(),'metrics':ranking.metrics(scores,ranking.meta.channel.to_numpy()),
            'model_artifacts':fingerprint(output/'model'),'loss':'Fully supervised weighted BCE; no consistency/semantic mechanism',
            'training_positive_rows':int(y.sum()),'training_negative_rows':int((y==0).sum()),
            'representation':'Learned normalized MCC auxiliary + original raw/no-channel residual core',
            'statistics_fields':list(STAT_NAMES) if config.mode=='statistics' else None}
    write_json(output/'result.json',result)
    print(f'v21-B final {config.mode} self{config.self_control} K{config.k} seed{config.seed} TP={result["metrics"]["tp_daily10"]}',flush=True)
    return result,scores


def run(source_root,gate_a_path,policy_path,output):
    gate_a=json.loads(Path(gate_a_path).read_text(encoding='utf8'))
    if gate_a.get('status')!='Gate_A_complete':raise ValueError('Completed measured Gate A required before historical model training')
    policy=json.loads(Path(policy_path).read_text(encoding='utf8'))
    if policy['epochs']!=12 or policy['seeds']!=[42,43,44] or policy['primary_k']!=6 or policy['sensitivity_k']!=[12,24]:
        raise ValueError('Frozen bounded B training/K policy required')
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    root=Path(__file__).resolve().parents[4]
    files=['app/risk/semantic_detection/v2/history.py','app/risk/semantic_detection/v2/history_data.py',
           'app/risk/semantic_detection/v2/history_experiment.py','app/risk/semantic_detection/v2/matched.py',
           'app/risk/semantic_detection/temporal.py','app/risk/semantic_detection/history_experiment.py',
           'app/risk/neural_v2.py','app/risk/neural_model.py','app/risk/ml_data.py','app/risk/ml_models.py',
           'app/risk/semantic_detection/data.py','app/risk/semantic_detection/common.py','app/risk/semantic_detection/evaluation.py',
           'app/risk/semantic_detection/semantics.py','app/cli/train_neural_v2.py']
    write_json(output/'protocol.json',{'policy':policy,'policy_sha256':digest(policy_path),'gate_A_sha256':digest(gate_a_path),
               'code':{name:digest(root/name) for name in files},'commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
               'python':platform.python_version(),'torch':torch.__version__,'numpy':np.__version__,
               'device':'cpu','dtype':'float32','threads':4,'final_2019_read':False,'paid_calls':0})
    shutil.copyfile(policy_path,output/'frozen-policy.json')
    for name in files:
        target=output/'frozen-code'/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(root/name,target)
    started=time.monotonic()
    try:
        loaded,provenance,facts=load_with_cards(source_root)
        (X,y,meta),(VX,vy,vmeta)=loaded
        write_json(output/'data.json',provenance)
        context=pd.concat([X,VX],ignore_index=True);context_meta=pd.concat([meta,vmeta],ignore_index=True)
        graph_start=time.monotonic()
        train_graph=historical_neighbors(meta,k=24,group='user_card',training=True)
        train_audit=verify_neighbors(meta,*train_graph,facts=facts,training=True)
        inference_graph=historical_neighbors(context_meta,k=24,group='user_card')
        inference_audit=verify_neighbors(context_meta,*inference_graph,facts=facts)
        for name,arrays in [('training',train_graph),('inference',inference_graph)]:
            np.save(output/f'{name}-neighbors24.npy',arrays[0]);np.save(output/f'{name}-ages24.npy',arrays[1])
        graph={'training':train_audit,'inference':inference_audit,'seconds':time.monotonic()-graph_start,
               'arrays_bytes':sum(a.nbytes for arrays in (train_graph,inference_graph) for a in arrays)}
        write_json(output/'graph.json',graph)
        primary_train=tuple(a[:,:6] for a in train_graph);primary_inference=tuple(a[:,:6] for a in inference_graph)
        train_stats=deterministic_statistics(X,*primary_train);infer_stats=deterministic_statistics(context,*primary_inference)
        np.save(output/'training-statistics.npy',train_stats);np.save(output/'inference-statistics.npy',infer_stats)
        ranking=Ranking(vmeta.assign(channel=VX.channel.to_numpy()),vy)
        targets=np.arange(len(X),len(context),dtype=np.int64)
        keys=[(UNKNOWN,UNKNOWN)]+sorted({(category_key(m,c)[0],UNKNOWN) for m,c in zip(X.mcc,X.channel)}-{(UNKNOWN,UNKNOWN)})
        fields={k:v for k,v in policy['config'].items() if k in Config.__dataclass_fields__}
        results={};ensembles={}
        def job(name,mode,self_control,k,seeds):
            records=[];predictions=[]
            for seed in seeds:
                config=Config(mode=mode,self_control=self_control,k=k,seed=seed,**fields)
                with threadpool_limits(limits=4):
                    record,scores=fit(config,policy['epochs'],X,y,keys,tuple(a[:,:k] for a in train_graph),
                        context,targets,tuple(a[:,:k] for a in inference_graph),ranking,output/name/f'seed-{seed}',
                        training_statistics=train_stats if mode=='statistics' else None,
                        inference_statistics=infer_stats if mode=='statistics' else None)
                records.append(record);predictions.append(scores)
            ensemble=np.mean(predictions,axis=0);ensembles[name]=ensemble
            np.save(output/name/'ensemble.npy',ensemble)
            results[name]={'seeds':records,'ensemble':ranking.metrics(ensemble,VX.channel.to_numpy())}
            write_json(output/name/'summary.json',results[name])
        job('flat','flat',False,6,policy['seeds'])
        job('statistics','statistics',False,6,policy['seeds'])
        for mode in policy['modes']:
            job(mode,mode,False,6,policy['seeds'])
            job(mode+'-self',mode,True,6,policy['seeds'])
        for k in policy['sensitivity_k']:
            job(f'mean-k{k}','mean',False,k,[42])
        references={}
        for name,file in {'frozen_residual':'runs/neural-v2-development-01/ensemble-development-scores.npy',
                          'strong_tree':'runs/hgb-raw-nochannel-v2-development-01/development-scores.npy'}.items():
            scores=np.load(Path(source_root)/file,allow_pickle=False);ensembles[name]=scores
            references[name]={'score_sha256':digest(Path(source_root)/file),'metrics':ranking.metrics(scores,VX.channel.to_numpy())}
        for name,file in {'previous_flat_B1':'runs/semantic-e1-additive-v2/B1/ensemble.npy',
                          'previous_history_mean_2epochs':'runs/semantic-e2-history-v1/mean/ensemble.npy',
                          'previous_history_attention_2epochs':'runs/semantic-e2-history-v1/attention/ensemble.npy'}.items():
            path=root/file;scores=np.load(path,allow_pickle=False);ensembles[name]=scores
            references[name]={'score_sha256':digest(path),'metrics':ranking.metrics(scores,VX.channel.to_numpy()),
                              'limit':'Historical channel-enabled architecture/user-only neighborhood and different budget; not matched causal comparison'}
        comparisons={}
        for name in results:
            if name.startswith('mean-k'):continue
            for baseline in ('frozen_residual','strong_tree','flat','statistics'):
                if name!=baseline:comparisons[name+'_vs_'+baseline]=ranking.compare(ensembles[baseline],ensembles[name])
        for mode in policy['modes']:
            if [r['parameters'] for r in results[mode]['seeds']]!=[r['parameters'] for r in results[mode+'-self']['seeds']]:
                raise ValueError('Exact-parameter control mismatch')
            comparisons[mode+'_vs_self']=ranking.compare(ensembles[mode+'-self'],ensembles[mode])
        report={'status':'Gate_B_complete','results':results,'references':references,'comparisons':comparisons,'graph':graph,
                'elapsed_seconds':time.monotonic()-started,'formal_primary_models':36,'single_seed_K_sensitivity_models':2,
                'default_detector_changed':False,'final_2019_read':False,'paid_calls':0,
                'limits':['2018 observed development, no independent external test','Fixed K6 primary; K12/24 sensitivities never used to select architecture',
                          'Same epochs/optimizer steps, not same FLOPs; history costs reported','Core point features retain historical user aggregates; new edges and statistics isolate user-card',
                          'No label availability proof; retrospective full supervision only','No semantic or consistency mechanism in Gate B']}
        write_json(output/'report.json',report);return report
    except Exception as exc:
        write_json(output/'failure.json',{'type':type(exc).__name__,'message':str(exc),'elapsed_seconds':time.monotonic()-started})
        raise
