"""Frozen 2x3 channel experiment and bounded A3 controls; no validation optimization."""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time

import numpy as np
import torch
from threadpoolctl import threadpool_limits

from app.risk.neural_model import seed_training
from app.risk.semantic_detection.common import digest, fingerprint, peak_working_set_bytes, write_json
from app.risk.semantic_detection.data import load_data
from app.risk.semantic_detection.evaluation import Ranking
from .matched import Config, Model, Predictor, Preprocessor, load
from .representations import table_for


def fit(config, keys, table, X, y, VX, ranking, output, epochs):
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    if epochs!=5 or not np.isin(y,[0,1]).all():
        raise ValueError('Gate A fixed five-epoch, fully supervised budget required')
    seed_training(config.seed,config.threads)
    prep=Preprocessor(config).fit(X,keys)
    tensors=prep.transform(X);target=torch.from_numpy(y.astype(np.float32))
    model=Model(config,table,tensors[0].shape[1],prep.numeric.categorical_sizes)
    predictor=Predictor(config,prep,model)
    optimizer=torch.optim.AdamW(model.parameters(),lr=config.learning_rate,weight_decay=config.weight_decay)
    loss_function=torch.nn.BCEWithLogitsLoss(pos_weight=torch.tensor(float(config.positive_weight)))
    generator=torch.Generator().manual_seed(config.seed)
    curves=[];started=time.monotonic()
    for epoch in range(1,epochs+1):
        model.train();order=torch.randperm(len(X),generator=generator);total=0.;max_gradient=0.
        for start in range(0,len(X),config.batch_size):
            positions=order[start:start+config.batch_size]
            optimizer.zero_grad(set_to_none=True)
            logits=model(*(v[positions] for v in tensors))
            loss=loss_function(logits,target[positions])
            if not torch.isfinite(loss):raise ValueError('Nonfinite training loss')
            loss.backward()
            norm=torch.nn.utils.clip_grad_norm_(model.parameters(),5.,error_if_nonfinite=True)
            max_gradient=max(max_gradient,float(norm))
            optimizer.step();total+=float(loss.detach())*len(positions)
        row={'epoch':epoch,'loss':total/len(X),'rows':len(X),'positives':int(y.sum()),
             'max_preclip_gradient_norm':max_gradient,'elapsed_seconds':time.monotonic()-started}
        curves.append(row);write_json(output/'curves.json',curves)
        print(f'v21 {config.representation} channel{config.include_channel} d{config.dimensions} seed{config.seed} epoch{epoch} loss={row["loss"]:.6f} seconds={row["elapsed_seconds"]:.1f}',flush=True)
    training_seconds=time.monotonic()-started
    predictor.save(output/'model')
    started=time.monotonic();scores=predictor.predict(VX);inference_seconds=time.monotonic()-started
    positions=np.random.default_rng(975).choice(len(VX),min(96,len(VX)),replace=False)
    replay=load(output/'model').predict(VX.iloc[positions])
    difference=float(np.max(np.abs(replay-scores[positions])))
    if difference>1e-6:raise ValueError('Reload prediction mismatch')
    np.save(output/'development-scores.npy',scores)
    result={'config':asdict(config),'epochs':epochs,'curves':curves,'training_seconds':training_seconds,
            'inference_seconds':inference_seconds,'inference_rows':len(VX),'reload_max_abs_diff':difference,
            'total_parameters':sum(p.numel() for p in model.parameters()),
            'trainable_parameters':sum(p.numel() for p in model.parameters() if p.requires_grad),
            'peak_process_working_set_bytes':peak_working_set_bytes(),
            'positive_gradient_weight':config.positive_weight,'negative_gradient_weight':1.,
            'metrics':ranking.metrics(scores,VX.channel.to_numpy()),'artifacts':fingerprint(output/'model')}
    write_json(output/'result.json',result)
    print(f'v21 final {config.representation} channel{config.include_channel} d{config.dimensions} seed{config.seed} TP={result["metrics"]["tp_daily10"]}',flush=True)
    return result,scores


def run(source_root, bank, policy_path, output):
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    policy=json.loads(Path(policy_path).read_text(encoding='utf8'))
    if policy['epochs']!=5 or policy['seeds']!=[42,43,44] or policy['engineered']:
        raise ValueError('Frozen Gate A budget/input contract required')
    code=Path(__file__).parent
    write_json(output/'protocol.json',{'policy':policy,'policy_sha256':digest(policy_path),'code':fingerprint(code),
               'git_commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
               'bank_manifest_sha256':digest(Path(bank)/'manifest.json'),'final_2019_read':False,'paid_calls':0})
    shutil.copyfile(policy_path,output/'frozen-policy.json')
    for name in fingerprint(code):
        target=output/'frozen-code'/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(code/name,target)
    started=time.monotonic()
    try:
        (X,y,meta),(VX,vy,vmeta)=(loaded:=load_data(source_root))[0]
        write_json(output/'data.json',loaded[1])
        write_json(output/'alignment.json',{'training_ids_sha256':hashlib.sha256('\n'.join(meta.transaction_id).encode()).hexdigest(),
                   'development_ids_sha256':hashlib.sha256('\n'.join(vmeta.transaction_id).encode()).hexdigest()})
        ranking=Ranking(vmeta,vy);results={};seed_scores={};ensemble_scores={}
        config_fields={k:v for k,v in policy.items() if k in Config.__dataclass_fields__ and k not in {'include_channel','dimensions','representation','seed'}}
        def job(name,representation,channel,dimension,seeds):
            mode=representation if representation in {'industry','description_shuffle'} else 'original'
            keys,table=table_for(bank,channel=channel,mode=mode,dimensions=dimension)
            records=results.get(name,{}).get('seeds',[])
            for seed in seeds:
                config=Config(representation=representation,include_channel=channel,dimensions=dimension,seed=seed,**config_fields)
                with threadpool_limits(limits=4):
                    record,scores=fit(config,keys,table,X,y,VX,ranking,output/name/f'seed-{seed}',policy['epochs'])
                records.append(record);seed_scores[(name,seed)]=scores
            declared=sorted(r['config']['seed'] for r in records)
            values=[seed_scores[(name,seed)] for seed in declared]
            ensemble=np.mean(values,axis=0);ensemble_scores[name]=ensemble
            np.save(output/name/'ensemble.npy',ensemble)
            results[name]={'seeds':records,'ensemble':ranking.metrics(ensemble,VX.channel.to_numpy()),'ensemble_seeds':declared}
            write_json(output/name/'summary.json',results[name])
        for channel in policy['include_channel']:
            for representation in policy['representations']:
                name=f'{"channel" if channel else "nochannel"}-{representation}-32'
                job(name,representation,channel,32,policy['seeds'])
        # 10 fixed one-seed diagnostic conditions, not an unrestricted search.
        for dimension in (128,256):
            for representation in ('semantic','learned','random'):
                job(f'channel-{representation}-{dimension}',representation,True,dimension,[42])
        for representation in ('industry','description_shuffle','category_shuffle','random'):
            job(f'channel-{representation}-32',representation,True,32,[42])
        candidates=[]
        for name in ('channel-semantic-128','channel-semantic-256','channel-industry-32'):
            dimension=int(name.rsplit('-',1)[1])
            controls=[f'channel-learned-{dimension}',f'channel-random-{dimension}']
            if 'industry' in name:controls.append('channel-semantic-32')
            score=lambda key:results[key]['seeds'][0]['metrics']
            baseline_tp=max(score(c)['tp_daily10'] for c in controls)
            baseline_ap=max(score(c)['ap'] for c in controls)
            if score(name)['tp_daily10']>baseline_tp and score(name)['ap']>baseline_ap:
                candidates.append((score(name)['tp_daily10']-baseline_tp,score(name)['ap'],name,controls))
        confirmation=None
        if candidates:
            _,_,name,controls=sorted(candidates,key=lambda v:(-v[0],-v[1],v[2]))[0]
            confirmation={'candidate':name,'controls':controls,'reason':'Fixed one-seed screening gate; development selection bias disclosed'}
            for key in [name,*controls]:
                missing=[s for s in (43,44) if (key,s) not in seed_scores]
                if missing:
                    _,representation,dimension=key.split('-')
                    job(key,representation,True,int(dimension),missing)
        frozen_path=Path(source_root)/'runs/neural-v2-development-01/ensemble-development-scores.npy'
        frozen=np.load(frozen_path,allow_pickle=False)
        comparisons={}
        for name,score in ensemble_scores.items():
            comparisons[name+'_vs_frozen']=ranking.compare(frozen,score)
        for channel in ('nochannel','channel'):
            baseline=ensemble_scores[f'{channel}-learned-32']
            comparisons[f'{channel}_semantic_vs_learned']=ranking.compare(baseline,ensemble_scores[f'{channel}-semantic-32'])
        reference=np.load(Path(source_root)/'runs/neural-v2-development-01/ensemble-development-scores.npy',allow_pickle=False)
        original_difference=float(np.max(np.abs(reference-ensemble_scores['nochannel-original-32'])))
        report={'status':'Gate_A2_A3_downstream_complete','results':results,'comparisons':comparisons,'screen_confirmation':confirmation,
                'original_residual_reproduction_max_abs_diff':original_difference,
                'original_residual_reproduction_matches':original_difference<=1e-6,
                'formal_matrix_models':18,'diagnostic_screen_models':10,'conditional_confirmation_models':sum(len(r['seeds']) for r in results.values())-28,
                'elapsed_seconds':time.monotonic()-started,'final_2019_read':False,'paid_calls':0,'default_detector_changed':False,
                'limits':['2018 observed development','Single-seed screens are not stability evidence','Conditional confirmation is development-selected',
                          'Active/frozen parameter counts differ; compare same-dimension random and learned controls','New normalized additive architecture differs from original E1']}
        write_json(output/'report.json',report)
        return report
    except Exception as exc:
        write_json(output/'failure.json',{'type':type(exc).__name__,'message':str(exc),'elapsed_seconds':time.monotonic()-started})
        raise
