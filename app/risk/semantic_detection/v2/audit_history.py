"""Independent full score replays, source-SQL statistics, gates and paired block uncertainty."""
from datetime import datetime
import json
import math
from pathlib import Path
import sqlite3
from contextlib import closing
import statistics as python_stats

import numpy as np
import pandas as pd
import torch
from threadpoolctl import threadpool_limits

from app.risk.ml_evaluation import evaluate_scores
from app.risk.ml_models import MODEL_COLUMNS
from app.risk.semantic_detection.common import digest,write_json
from app.risk.semantic_detection.evaluation import Ranking
from .audit_matched import manual_ap
from .history import STAT_NAMES
from .history_data import load_with_cards,verify_neighbors
from .history_experiment import load


def block_interval(daily,*,block=7,repetitions=5000,seed=1217):
    values=np.asarray(daily,dtype=np.int64)
    if values.ndim!=1 or len(values)==0 or not 1<=block<=len(values):raise ValueError('Known ordered paired days required')
    starts=np.random.default_rng(seed).integers(0,len(values),size=(repetitions,math.ceil(len(values)/block)))
    indices=((starts[:,:,None]+np.arange(block))%len(values)).reshape(repetitions,-1)[:,:len(values)]
    draws=values[indices].sum(1)
    return {'net':int(values.sum()),'paired_circular_block_95':np.quantile(draws,[.025,.975]).tolist(),
            'paired_circular_block_99_descriptive':np.quantile(draws,[.005,.995]).tolist(),
            'block_days':block,'repetitions':repetitions,'seed':seed,
            'limit':'Posthoc fixed sensitivity to local temporal dependence; not an independent or search-adjusted test'}


def verify_statistics_sql(frame,metadata,values,facts,*,samples=256):
    selected=np.random.default_rng(1801).choice(len(frame),min(samples,len(frame)),replace=False)
    max_difference=0.
    with closing(sqlite3.connect(Path(facts).resolve().as_uri()+'?mode=ro',uri=True)) as db:
        db.execute('PRAGMA query_only=ON')
        for i in selected:
            row=metadata.iloc[i];current=frame.iloc[i]
            rows=db.execute('''SELECT amount_cents,mcc,event_time FROM transactions
                WHERE user_id=? AND card_id=? AND event_time<? AND event_time>='2016-01-01'
                AND split IN ('train','validation') ORDER BY event_time DESC,transaction_id DESC LIMIT 6''',
                (str(row.user_id),str(row.card_id),str(row.event_time))).fetchall()
            expected=np.zeros(14)
            if rows:
                amounts=[float(r[0]) for r in rows]
                ages=np.array([(datetime.fromisoformat(row.event_time)-datetime.fromisoformat(r[2])).total_seconds() for r in rows],dtype=np.float32)
                matches=[str(r[1])==str(current.mcc) for r in rows]
                absolute=python_stats.mean([abs(a) for a in amounts])
                expected=np.array([len(rows),python_stats.mean(amounts),python_stats.pstdev(amounts),min(amounts),max(amounts),
                       python_stats.median(amounts),absolute,float(current.amount_cents)/max(absolute,100),
                       float(current.amount_cents)-amounts[0],python_stats.mean(matches),matches[0],
                       float(ages.sum()/len(ages)),float(ages[0]),float(ages.max())])
            np.testing.assert_allclose(values[i,:11],expected[:11],rtol=1e-6,atol=1e-4)
            np.testing.assert_allclose(values[i,11:],expected[11:],rtol=1e-6,atol=2.)
            max_difference=max(max_difference,float(np.max(np.abs(values[i]-expected))))
    return {'independent_SQL_targets':len(selected),'max_raw_stat_abs_difference':max_difference,
            'statistics':list(STAT_NAMES),'float32_age_rounding_disclosed':True}


class GateProbe:
    """Read-only hooks; never replace outputs or pass evaluation labels into the model."""
    def __init__(self,model,labels):
        self.model,self.labels=model,np.asarray(labels);self.offset=0;self.state={};self.acc={}
        self.handles=[model.register_forward_pre_hook(self.before,with_kwargs=True),model.register_forward_hook(self.after)]
        if hasattr(model,'gate'):self.handles.append(model.gate.register_forward_hook(self.gate))
        if model.config.mode=='attention':
            self.handles.append(model.query.register_forward_hook(lambda m,a,o:self.capture('query',o)))
            self.handles.append(model.key.register_forward_hook(lambda m,a,o:self.capture('key',o)))

    def capture(self,key,value):
        self.state[key]=value.detach()

    def before(self,module,args,kwargs):
        n=len(args[0]);self.state={'labels':self.labels[self.offset:self.offset+n]}
        if module.config.self_control:
            self.state['mask']=torch.ones(n,module.config.k,dtype=torch.bool)
            self.state['ages']=torch.arange(1,module.config.k+1).expand(n,-1)*3600.
        elif kwargs.get('history') is not None:
            self.state['mask']=kwargs['history'][-2];self.state['ages']=kwargs['history'][-1]

    def gate(self,module,args,output):
        features=args[0];width=features.shape[1]//2
        metrics={'context_gate_fraction':(1-output.sigmoid()).mean(1),
                 'context_to_target_norm':features[:,width:].norm(dim=1)/features[:,:width].norm(dim=1).clamp_min(1e-8)}
        mask=self.state['mask'];valid=mask.any(1)
        if 'query' in self.state:
            q,k=self.state['query'],self.state['key']
            logits=(q[:,None,:]*k).sum(-1)/width**.5-torch.log1p(self.state['ages']/3600.)
            weight=logits.masked_fill(~mask,-1e9).softmax(1)*mask
            weight=weight/weight.sum(1,keepdim=True).clamp_min(1e-8)
            metrics['attention_entropy']=-(weight*weight.clamp_min(1e-12).log()).sum(1)
            metrics['attention_effective_neighbors']=1/weight.square().sum(1).clamp_min(1e-8)
            metrics['attention_newest_weight']=weight[:,0]
        for label in (0,1):
            selected=valid&torch.from_numpy(self.state['labels']==label)
            count=int(selected.sum())
            if not count:continue
            record=self.acc.setdefault(str(label),{'rows':0,'sums':{}});record['rows']+=count
            for name,value in metrics.items():record['sums'][name]=record['sums'].get(name,0.)+float(value[selected].double().sum())

    def after(self,module,args,output):
        self.offset+=len(output)

    def finish(self):
        for handle in self.handles:handle.remove()
        return {label:{'rows':r['rows'],**{name:value/r['rows'] for name,value in r['sums'].items()}} for label,r in self.acc.items()}


def run(source_root,history_run,tree_run,output):
    history_run,output=Path(history_run),Path(output);output.mkdir(parents=True,exist_ok=False)
    report=json.loads((history_run/'report.json').read_text(encoding='utf8'))
    if report['status']!='Gate_B_complete':raise ValueError('Completed registered training required')
    protocol=json.loads((history_run/'protocol.json').read_text(encoding='utf8'))
    root=Path(__file__).resolve().parents[4]
    for name,value in protocol['code'].items():
        if digest(history_run/'frozen-code'/name)!=value or digest(root/name)!=value:
            raise ValueError('Training/source implementation changed since frozen run: '+name)
    loaded,provenance,facts=load_with_cards(source_root)
    (X,y,meta),(VX,vy,vmeta)=loaded
    context=pd.concat([X,VX],ignore_index=True);context_meta=pd.concat([meta,vmeta],ignore_index=True)
    train_graph=tuple(np.load(history_run/f'training-{name}24.npy',mmap_mode='r') for name in ('neighbors','ages'))
    infer_graph=tuple(np.load(history_run/f'inference-{name}24.npy',mmap_mode='r') for name in ('neighbors','ages'))
    graph={'train':verify_neighbors(meta,*train_graph,facts=facts,training=True),
           'inference':verify_neighbors(context_meta,*infer_graph,facts=facts)}
    graph['prefixes']={str(k):{'training_edges':int((train_graph[0][:,:k]>=0).sum()),
                            'inference_edges':int((infer_graph[0][:,:k]>=0).sum())} for k in (6,12,24)}
    statistics=np.load(history_run/'inference-statistics.npy',mmap_mode='r')
    statistic_proof=verify_statistics_sql(context,context_meta,statistics,facts)
    columns=[c for c in MODEL_COLUMNS if c not in ('mcc','channel')]
    raw=X[columns].to_numpy(dtype=np.float64);z=np.sign(raw)*np.log1p(np.abs(raw))
    train_median=np.nan_to_num(np.nanmedian(z,axis=0));q25,q75=np.nanpercentile(z,[25,75],axis=0)
    train_scale=np.maximum(np.nan_to_num(q75-q25),.25)
    train_mcc={v:i+1 for i,v in enumerate(sorted(set(X.mcc.fillna('<MISSING>').astype(str))))}
    ranking=Ranking(vmeta,vy);records={};scores_by_name={};max_difference=0.;torch.set_num_threads(4)
    for name,value in report['results'].items():
        predictions=[];seed_records=[]
        for row in value['seeds']:
            config=row['config'];folder=history_run/name/f'seed-{config["seed"]}'
            if row['epochs']!=12 or len(row['curves'])!=12 or row['training_positive_rows']!=int(y.sum()):
                raise ValueError('Actual epoch or class exposure budget differs')
            for e,curve in enumerate(row['curves']):
                expected_lr=.00005+(.001-.00005)*(1+math.cos(math.pi*e/12))/2
                if curve['rows']!=len(X) or curve['positive_rows']!=int(y.sum()) or abs(curve['learning_rate']-expected_lr)>1e-12:
                    raise ValueError('Optimization budget/schedule mismatch')
            for file,sha in row['model_artifacts'].items():
                if digest(folder/'model'/file)!=sha:raise ValueError('Model bytes changed')
            predictor=load(folder/'model')
            if predictor.prep.numeric.categories!=('mcc',) or any(c!='unknown' for _,c in predictor.prep.keys):
                raise ValueError('History study uses forbidden explicit channel branch')
            numeric=predictor.prep.numeric
            if numeric.columns!=columns or not np.array_equal(numeric.median,train_median) or not np.array_equal(numeric.scale,train_scale) or numeric.vocabularies!={'mcc':train_mcc}:
                raise ValueError('History point preprocessing is not exactly training-fitted')
            if config['mode']=='statistics':
                raw_stats=statistics[:len(X)].astype(np.float64);v=np.sign(raw_stats)*np.log1p(np.abs(raw_stats))
                a,b=np.percentile(v,[25,75],axis=0)
                if not np.array_equal(predictor.scaler.median,np.median(v,axis=0)) or not np.array_equal(predictor.scaler.scale,np.maximum(b-a,.25)):
                    raise ValueError('Statistics preprocessing not fitted exclusively on training')
            if sum(p.numel() for p in predictor.model.parameters())!=row['parameters']:raise ValueError('Parameter count differs')
            probe=GateProbe(predictor.model,vy)
            with threadpool_limits(limits=4):
                replay=predictor.predict(context,np.arange(len(X),len(context)),
                    neighborhood=tuple(a[:,:config['k']] for a in infer_graph),statistics=statistics)
            gates=probe.finish()
            expected=np.load(folder/'development-scores.npy',allow_pickle=False)
            difference=float(np.max(np.abs(replay-expected)));max_difference=max(max_difference,difference)
            if difference>1e-6:raise ValueError('Full 422088-row replay differs')
            predictions.append(expected);seed_records.append({'seed':config['seed'],'rows':len(replay),'max_abs_diff':difference,'gate_probe':gates})
            print(f'B-audit {name} seed{config["seed"]} full_rows={len(replay)} diff={difference:g}',flush=True)
        ensemble=np.mean(predictions,axis=0)
        if not np.array_equal(ensemble,np.load(history_run/name/'ensemble.npy',allow_pickle=False)):raise ValueError('Missing or reordered seeds')
        ap=manual_ap(vy,ensemble)
        metrics,queue=evaluate_scores(vmeta,vy,ensemble)
        if abs(ap-value['ensemble']['ap'])>1e-12 or metrics['daily_review']['true_positives']!=value['ensemble']['tp_daily10']:
            raise ValueError('Independent full ranking/AP differs')
        scores_by_name[name]=ensemble
        records[name]={'seed_replays':seed_records,'manual_AP':ap,'heap_daily10_TP':metrics['daily_review']['true_positives'],'reviewed':len(queue)}
    for name,file in {'frozen_residual':Path(source_root)/'runs/neural-v2-development-01/ensemble-development-scores.npy',
                      'strong_tree':Path(source_root)/'runs/hgb-raw-nochannel-v2-development-01/development-scores.npy',
                      'A2_strong_flat_5epochs':root/'runs/semantic-v21-a2-01/nochannel-learned-32/ensemble.npy',
                      'posthoc_statistics_tree':Path(tree_run)/'development-scores.npy'}.items():
        scores_by_name[name]=np.load(file,allow_pickle=False)
    comparisons={}
    for mode in protocol['policy']['modes']:
        left=report['results'][mode]['seeds'];right=report['results'][mode+'-self']['seeds']
        if [r['parameters'] for r in left]!=[r['parameters'] for r in right]:raise ValueError('Capacity control differs')
        for baseline in ('flat',mode+'-self','statistics','frozen_residual','A2_strong_flat_5epochs','posthoc_statistics_tree'):
            comparison=ranking.compare(scores_by_name[baseline],scores_by_name[mode])
            comparison['block_sensitivity']=block_interval(list(comparison['daily_net'].values()))
            comparisons[mode+'_vs_'+baseline]=comparison
    result={'status':'Gate_B_independently_verified','full_model_replays':sum(len(r['seed_replays']) for r in records.values()),
            'rows_per_full_replay':len(VX),'max_full_replay_abs_diff':max_difference,'conditions':records,
            'graph':graph,'source_SQL_statistics':statistic_proof,'comparisons':comparisons,
            'report_sha256':digest(history_run/'report.json'),'data':provenance,'final_2019_read':False,'paid_calls':0,
            'gate_probe_limit':'Read-only activation statistics, not causal explanations; labels used only by the host to group audit output'}
    write_json(output/'report.json',result);return result
