"""Final registered posthoc broad-statistic controls; no outcome-driven feature selection."""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import time

import joblib
import numpy as np
import pandas as pd
import torch
from torch import nn
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OrdinalEncoder
from threadpoolctl import threadpool_limits

from app.risk.neural_model import seed_training
from app.risk.semantic_detection.common import digest,write_json,peak_working_set_bytes
from app.risk.semantic_detection.data import load_data
from app.risk.semantic_detection.evaluation import Ranking
from app.risk.semantic_detection.semantics import UNKNOWN,category_key
from .matched import Config,Model as PointModel,Preprocessor
from .history import STAT_NAMES


def features(numeric,categories,neighbors,category_size):
    mask=neighbors>=0;safe=neighbors.clip(min=0);count=mask.sum(1);den=np.maximum(count,1)
    past=np.where(mask[:,:,None],numeric[safe],0.)
    mean=past.sum(1,dtype=np.float64)/den[:,None]
    std=np.sqrt(np.maximum((past.astype(np.float64)**2).sum(1)/den[:,None]-mean**2,0))
    latest=np.where(mask[:,0,None],past[:,0],0.)
    frequency=np.zeros((len(neighbors),category_size),dtype=np.float32)
    rows=np.broadcast_to(np.arange(len(neighbors))[:,None],neighbors.shape)
    np.add.at(frequency,(rows[mask],categories[safe][mask]),1.)
    frequency/=den[:,None]
    return mean,std,latest,frequency,count


class BroadStatsModel(nn.Module):
    def __init__(self,config,prep,dimensions):
        super().__init__();self.config=config
        self.point=PointModel(config,np.zeros((len(prep.keys),32),dtype=np.float32),len(prep.numeric.columns)*2,prep.numeric.categorical_sizes)
        with torch.random.fork_rng(devices=[]):
            torch.manual_seed(config.seed+404)
            self.context=nn.Sequential(nn.Linear(dimensions,32),nn.GELU(),nn.Linear(32,config.width))
            self.gate=nn.Linear(config.width*2,config.width)

    def forward(self,numeric,categories,pairs,statistics):
        hidden=self.point.encode(numeric,categories,pairs);context=self.context(statistics)
        gate=self.gate(torch.cat([hidden,context],1)).sigmoid()
        return self.point.core.head(gate*hidden+(1-gate)*context).squeeze(-1)


def predict(model,prep,frame,statistics):
    tensors=prep.transform(frame);scores=[];model.eval()
    with torch.inference_mode():
        for start in range(0,len(frame),4096):
            end=start+4096
            stat=torch.from_numpy(np.array(statistics[start:end],dtype=np.float32,copy=True))
            values=model(*(v[start:end] for v in tensors),stat).sigmoid().numpy()
            if not np.isfinite(values).all():raise ValueError('Nonfinite broad-statistic prediction')
            scores.append(values)
    return np.concatenate(scores)


def run(source_root,history_run,output):
    output=Path(output);output.mkdir(parents=True,exist_ok=False);history_run=Path(history_run)
    write_json(output/'protocol.json',{'type':'Final posthoc broad-statistic challenge after Mean215 and narrow-statistic tree127',
         'main_history_protocol_sha256':digest(history_run/'protocol.json'),'code_sha256':digest(__file__),
         'features':'14 fixed + 24 normalized input means/std/latest/target-minus-mean + all training MCC frequencies',
         'neural':'32 bottleneck + gate, three seeds42/43/44,12epochs,BCE20,AdamW,cosine; capacity slightly greater thanMean',
         'tree':'100 iterations,15leaves,minleaf50,L2=1,balanced,seed42; no tuning',
         'threads':2,'further_model_search':False,'paid_calls':0,'posthoc':True})
    try:
        (X,y,meta),(VX,vy,vmeta)=(loaded:=load_data(source_root))[0]
        graph=json.loads((history_run/'graph.json').read_text(encoding='utf8'))
        if hashlib.sha256('\n'.join(meta.transaction_id).encode()).hexdigest()!=graph['training']['node_ids_sha256']:
            raise ValueError('Broad-statistic source alignment differs')
        context=pd.concat([X,VX],ignore_index=True)
        ids=pd.concat([meta,vmeta],ignore_index=True).transaction_id
        if hashlib.sha256('\n'.join(ids).encode()).hexdigest()!=graph['inference']['node_ids_sha256']:
            raise ValueError('Full source context ordering differs')
        keys=[(UNKNOWN,UNKNOWN)]+sorted({(category_key(m,c)[0],UNKNOWN) for m,c in zip(X.mcc,X.channel)}-{(UNKNOWN,UNKNOWN)})
        base_config=Config(representation='learned',include_channel=False,threads=2)
        prep=Preprocessor(base_config).fit(X,keys);tensors=prep.transform(context)
        numeric=tensors[0].numpy();categories=tensors[1][:,0].numpy();category_size=prep.numeric.categorical_sizes[0]
        neighbors=np.load(history_run/'inference-neighbors24.npy',mmap_mode='r')[:,:6]
        old=np.load(history_run/'inference-statistics.npy',mmap_mode='r')
        dimension=14+numeric.shape[1]*4+category_size
        begin=time.monotonic()
        raw=np.lib.format.open_memmap(output/'broad-raw.npy',mode='w+',dtype=np.float32,shape=(len(context),dimension))
        for start in range(0,len(context),16384):
            end=min(start+16384,len(context));idx=neighbors[start:end]
            mean,std,latest,frequency,count=features(numeric,categories,idx,category_size)
            delta=np.where((count>0)[:,None],numeric[start:end]-mean,0.)
            values=np.concatenate([old[start:end],mean,std,latest,delta,frequency],axis=1)
            if not np.isfinite(values).all():raise ValueError('Nonfinite broad statistic')
            raw[start:end]=values.astype(np.float32)
        raw.flush();construction_seconds=time.monotonic()-begin
        medians=np.zeros(dimension);scales=np.zeros(dimension)
        for j in range(dimension):
            column=np.asarray(raw[:len(X),j],dtype=np.float64)
            z=np.sign(column)*np.log1p(np.abs(column));medians[j]=np.median(z)
            a,b=np.percentile(z,[25,75]);scales[j]=max(b-a,.25)
        normalized=np.lib.format.open_memmap(output/'broad-normalized.npy',mode='w+',dtype=np.float32,shape=raw.shape)
        for start in range(0,len(context),16384):
            values=np.asarray(raw[start:start+16384],dtype=np.float64);z=np.sign(values)*np.log1p(np.abs(values))
            normalized[start:start+16384]=np.clip((z-medians)/scales,-8,8).astype(np.float32)
        normalized.flush()
        encoded_names=list(prep.numeric.columns)+[c+'_missing' for c in prep.numeric.columns]
        inverse={v:k for k,v in prep.numeric.vocabularies['mcc'].items()}
        feature_names=['prior_'+n for n in STAT_NAMES]+[f'{kind}_{n}' for kind in ('mean','std','latest','current_minus_mean') for n in encoded_names]+[
            'mcc_frequency_'+inverse.get(i,'unknown') for i in range(category_size)]
        write_json(output/'features.json',{'rows':len(context),'training_rows':len(X),'dimensions':dimension,'feature_names':feature_names,'construction_seconds':construction_seconds,
             'raw_sha256':digest(output/'broad-raw.npy'),'normalized_sha256':digest(output/'broad-normalized.npy'),
             'training_only_median':medians.tolist(),'training_only_scale':scales.tolist(),'training_mcc_categories':category_size,
             'no_identity_channel_or_labels':True,'past_scope':'Same source-verified user-card strictly-past last6 as registered study'})
        del tensors
        ranking=Ranking(vmeta,vy);results={};nn_scores=[]
        for seed in (42,43,44):
            folder=output/f'neural-seed-{seed}';folder.mkdir()
            config=Config(representation='learned',include_channel=False,seed=seed,threads=2)
            seed_training(seed,2);model=BroadStatsModel(config,prep,dimension);train_tensors=prep.transform(X)
            target=torch.from_numpy(y.astype(np.float32));generator=torch.Generator().manual_seed(seed)
            optimizer=torch.optim.AdamW(model.parameters(),lr=.001,weight_decay=.01)
            scheduler=torch.optim.lr_scheduler.CosineAnnealingLR(optimizer,T_max=12,eta_min=.00005)
            curves=[];started=time.monotonic()
            with threadpool_limits(limits=2):
                for epoch in range(1,13):
                    model.train();order=torch.randperm(len(X),generator=generator);total=0.;lr=optimizer.param_groups[0]['lr']
                    for start in range(0,len(X),4096):
                        positions=order[start:start+4096]
                        stat=torch.from_numpy(np.array(normalized[positions.numpy()],dtype=np.float32,copy=True))
                        optimizer.zero_grad(set_to_none=True)
                        logits=model(*(t[positions] for t in train_tensors),stat)
                        loss=nn.functional.binary_cross_entropy_with_logits(logits,target[positions],pos_weight=torch.tensor(20.))
                        if not torch.isfinite(loss):raise ValueError('Nonfinite broad-statistic loss')
                        loss.backward();torch.nn.utils.clip_grad_norm_(model.parameters(),5.,error_if_nonfinite=True)
                        optimizer.step();total+=float(loss.detach())*len(positions)
                    curves.append({'epoch':epoch,'loss':total/len(X),'lr':lr,'rows':len(X),'positives':int(y.sum()),'elapsed_seconds':time.monotonic()-started})
                    write_json(folder/'curves.json',curves);scheduler.step()
                    torch.save({'epoch':epoch,'state_dict':model.state_dict(),'optimizer':optimizer.state_dict(),'rng':torch.get_rng_state()},folder/f'epoch-{epoch:02d}.pt')
                    print(f'broad-statistics seed{seed} epoch{epoch} loss={curves[-1]["loss"]:.6f}',flush=True)
            seconds=time.monotonic()-started
            torch.save({'config':asdict(config),'dimensions':dimension,'state_dict':model.state_dict()},folder/'model.pt')
            joblib.dump(prep,folder/'preprocessor.joblib')
            begin=time.monotonic()
            with threadpool_limits(limits=2):scores=predict(model,prep,VX,normalized[len(X):])
            inference=time.monotonic()-begin
            state=torch.load(folder/'model.pt',weights_only=True,map_location='cpu')
            restored=BroadStatsModel(Config(**state['config']),joblib.load(folder/'preprocessor.joblib'),dimension)
            restored.load_state_dict(state['state_dict'])
            sample=np.random.default_rng(975).choice(len(VX),96,replace=False)
            with threadpool_limits(limits=2):replay=predict(restored,prep,VX.iloc[sample],normalized[len(X)+sample])
            difference=float(np.max(np.abs(replay-scores[sample])))
            if difference>1e-6:raise ValueError('Broad-statistic reload differs')
            np.save(folder/'development-scores.npy',scores);nn_scores.append(scores)
            record={'seed':seed,'metrics':ranking.metrics(scores,VX.channel.to_numpy()),'train_seconds':seconds,'inference_seconds':inference,
                    'reload_max_abs_diff':difference,'parameters':sum(p.numel() for p in model.parameters()),'curves':curves,
                    'peak_process_bytes':peak_working_set_bytes()}
            write_json(folder/'result.json',record);results[f'neural-seed-{seed}']=record
        ensemble=np.mean(nn_scores,axis=0);np.save(output/'neural-ensemble.npy',ensemble)
        results['neural_ensemble']=ranking.metrics(ensemble,VX.channel.to_numpy())
        # Fixed raw broad statistics + current raw fields and MCC; force numeric float32 to bound memory.
        current=[c for c in X if c not in ('mcc','channel')];stat_names=feature_names
        def tree_frame(frame,array):
            numeric_frame=pd.concat([frame[current].astype(np.float32).reset_index(drop=True),
                pd.DataFrame(np.asarray(array),columns=stat_names)],axis=1)
            numeric_frame['mcc']=frame.mcc.astype(str).to_numpy();return numeric_frame
        AX=tree_frame(X,raw[:len(X)]);AVX=tree_frame(VX,raw[len(X):])
        transformer=ColumnTransformer([('numeric','passthrough',current+stat_names),
              ('mcc',OrdinalEncoder(dtype=np.float32,handle_unknown='use_encoded_value',unknown_value=-1),['mcc'])])
        tree=Pipeline([('preprocess',transformer),('model',HistGradientBoostingClassifier(max_iter=100,max_leaf_nodes=15,learning_rate=.1,
             min_samples_leaf=50,l2_regularization=1.,early_stopping=False,class_weight='balanced',random_state=42,
             categorical_features=[False]*(len(current)+dimension)+[True]))])
        begin=time.monotonic()
        with threadpool_limits(limits=2):tree.fit(AX,y)
        seconds=time.monotonic()-begin;begin=time.monotonic()
        with threadpool_limits(limits=2):tree_scores=tree.predict_proba(AVX)[:,1]
        inference=time.monotonic()-begin;joblib.dump(tree,output/'tree.joblib',compress=3)
        np.save(output/'tree-development-scores.npy',tree_scores)
        results['tree']={'metrics':ranking.metrics(tree_scores,VX.channel.to_numpy()),'train_seconds':seconds,'inference_seconds':inference}
        result={'status':'posthoc_broad_statistic_controls_complete','results':results,'dimensions':dimension,'posthoc':True,
                'model_runs':4,'data':loaded[1],'final_2019_read':False,'paid_calls':0,
                'limits':['Added after observing Mean215 and narrow-statistic tree127; strengthens challenge but not preregistered',
                          'Neural capacity slightly greater thanMean; tree loss and capacity differ','No further feature/model search allowed']}
        write_json(output/'report.json',result);return result
    except Exception as exc:
        write_json(output/'failure.json',{'type':type(exc).__name__,'message':str(exc)});raise
