"""Frozen A2 run; host reads only independently verified inert JSONL."""
import argparse
import copy
import json
from pathlib import Path
import subprocess
import time
import joblib
import numpy as np
import torch
from sklearn.metrics import average_precision_score
from threadpoolctl import threadpool_limits
from app.risk.behavior_semantic.baselines import Classical,fit_net,prepare,score_net,numeric
from app.risk.behavior_semantic.study import read_records,write,sha,metrics,development_threshold


def main():
    p=argparse.ArgumentParser();p.add_argument('--protocol',required=True);p.add_argument('--output',required=True);a=p.parse_args()
    policy=json.loads(Path(a.protocol).read_text(encoding='utf8'))
    if policy['status']!='frozen_before_A2_training' or not policy['conversion_independently_verified'] or policy['permission_source']!='https://github.com/pboulieris/FraudNLP/issues/4#issuecomment-2514497586':raise ValueError('A1 reviewed freeze required')
    if subprocess.check_output(['git','status','--porcelain']).strip():raise ValueError('Commit protocol/code before fitting')
    for name,h in policy['code_sha256'].items():
        if sha(name)!=h:raise ValueError('Frozen source changed '+name)
    out=Path(a.output);out.mkdir(parents=True,exist_ok=False);write(out/'protocol.json',policy)
    if sha(policy['records'])!=policy['records_sha256'] or sha(policy['split'])!=policy['split_sha256']:raise ValueError('Input bytes changed')
    rows,_=read_records(policy['records']);split=np.load(policy['split'],allow_pickle=False)
    train=[rows[int(i)] for i in split['train']];dev=[rows[int(i)] for i in split['development']]
    y=np.asarray([r['is_fraud'] for r in train]);dy=np.asarray([r['is_fraud'] for r in dev])
    artifacts={};dev_scores={};records={};begin=time.monotonic()
    for name in policy['models']:
        for seed in policy['seeds']:
            folder=out/name/f'seed-{seed}';folder.mkdir(parents=True);tick=time.monotonic();cpu=time.process_time();key=f'{name}/seed-{seed}'
            if name in ('gru','transformer'):
                model,lookup,prep,record=fit_net(train,y,dev,dy,kind=name,seed=seed,max_epochs=policy['epochs'],patience=policy['patience'],vocab_ids=range(1916),checkpoint_dir=folder)
                torch.save({'kind':name,'vocab':len(lookup)+2,'state_dict':model.state_dict()},folder/'model.pt');joblib.dump({'lookup':lookup,'prep':prep},folder/'preparation.joblib')
                ds,dt=prepare(dev,lookup);scores=score_net(model,ds,dt,np.zeros((len(dev),0),np.float32))
                artifacts[key]={'kind':'torch','folder':str(folder)}
            else:
                candidates=[];best=-1.;winner=None;scores=None
                grid=[(1.,w) for w in (False,True)] if name=='numeric_tree' else [(C,w) for C in policy['C'] for w in (False,True)]
                for C,balanced in grid:
                    with threadpool_limits(limits=2):model=Classical(name,seed,C,balanced).fit(train,y);pred=model.predict(dev)
                    ap=float(average_precision_score(dy,pred));candidates.append({'C':C,'balanced':balanced,'development_AP':ap})
                    if ap>best:best=ap;winner=model;scores=pred
                joblib.dump(winner,folder/'model.joblib');record={'candidates':candidates,'selected_C':winner.C,'selected_balanced':winner.balanced,'best_development_AP':best}
                artifacts[key]={'kind':'classical','folder':str(folder)}
            record.update(seed=seed,wall_seconds=time.monotonic()-tick,CPU_seconds=time.process_time()-cpu,test_seen_by_trainer=False)
            np.save(folder/'development-scores.npy',scores);dev_scores[key]=scores;records[key]=record;write(folder/'training.json',record)
            print(key,'training_complete',record['best_development_AP'],flush=True)
    write(out/'all-training-complete.json',{'instances':len(artifacts),'artifacts':artifacts,'frozen_protocol_sha256':sha(a.protocol)})
    if not policy.get('release_final_test',False):
        summaries={}
        for name in policy['models']:
            score=np.mean([dev_scores[f'{name}/seed-{s}'] for s in policy['seeds']],axis=0)
            summaries[name]=metrics(dy,score,development_threshold(dy,score))
            np.save(out/name/'ensemble-development.npy',score)
        result={'status':'A2_development_comparison_complete_test_still_sealed','records':records,'development_ensembles':summaries,
            'protocol_sha256':sha(a.protocol),'wall_seconds':time.monotonic()-begin,'final_test_predictions_generated':False,
            'scope':'Static sequence-grouped development only; A3 decisions may use development, never final test',
            'default_detector_changed':False,'paid_calls':0}
        write(out/'development-results.json',result);print(json.dumps({'status':result['status'],'development':summaries}));return
    # Only now score test checkpoints; test labels never select hyperparameters/epochs.
    test=[rows[int(i)] for i in split['test']];ty=np.asarray([r['is_fraud'] for r in test]);evaluations={};scores_by_model={};maximum=0.
    for key,artifact in artifacts.items():
        folder=Path(artifact['folder']);tick=time.monotonic();cpu=time.process_time()
        if artifact['kind']=='classical':model=joblib.load(folder/'model.joblib');scores=model.predict(test);replayed_dev=model.predict(dev)
        else:
            from app.risk.behavior_semantic.baselines import BehaviorNet
            state=torch.load(folder/'model.pt',weights_only=True);pr=joblib.load(folder/'preparation.joblib');model=BehaviorNet(state['vocab'],state['kind']);model.load_state_dict(state['state_dict']);seq,dt=prepare(test,pr['lookup']);scores=score_net(model,seq,dt,np.zeros((len(test),0),np.float32));ds,dts=prepare(dev,pr['lookup']);replayed_dev=score_net(model,ds,dts,np.zeros((len(dev),0),np.float32))
        wall=time.monotonic()-tick;CPU=time.process_time()-cpu;diff=float(np.max(np.abs(replayed_dev-dev_scores[key])))
        if diff>1e-6:raise ValueError('Saved model development replay differs')
        maximum=max(maximum,diff);threshold=development_threshold(dy,dev_scores[key]);value=metrics(ty,scores,threshold)
        value.update(scoring_and_dev_replay_wall_seconds=wall,scoring_and_dev_replay_CPU_seconds=CPU,full_dev_reload_max_abs_error=diff)
        np.save(folder/'test-scores.npy',scores);write(folder/'evaluation.json',value);evaluations[key]=value;scores_by_model[key]=scores
    ensembles={}
    for name in policy['models']:
        test_avg=np.mean([scores_by_model[f'{name}/seed-{s}'] for s in policy['seeds']],axis=0)
        dev_avg=np.mean([dev_scores[f'{name}/seed-{s}'] for s in policy['seeds']],axis=0)
        ensembles[name]=metrics(ty,test_avg,development_threshold(dy,dev_avg));np.save(out/name/'ensemble-test.npy',test_avg)
    result={'status':'A2_frozen_comparison_complete','records':records,'seed_evaluations':evaluations,'ensembles':ensembles,'max_full_development_replay_error':maximum,
        'protocol_sha256':sha(a.protocol),'wall_seconds':time.monotonic()-begin,'scope':'Static anonymous sequence-grouped split; neither unseen users nor future periods; published RFM provenance not independently verified',
        'default_detector_changed':False,'paid_calls':0}
    write(out/'results.json',result);print(json.dumps({'status':result['status'],'ensembles':ensembles}))


if __name__=='__main__':main()
