"""One-time final scoring after development choices/A4 decision are frozen."""
import argparse
from collections import Counter
import json
from pathlib import Path
import time
import joblib
import numpy as np
import torch
from app.risk.behavior_semantic.baselines import BehaviorNet,prepare,score_net,events
from app.risk.behavior_semantic.semantics import history_shuffle
from app.risk.behavior_semantic.study import read_records,sha,write,metrics,development_threshold


def manual_ap(y,s):
    order=np.argsort(-s,kind='stable');yy=np.asarray(y)[order];ss=np.asarray(s)[order]
    ends=np.r_[np.flatnonzero(np.diff(ss)!=0),len(ss)-1];tp=np.cumsum(yy)[ends]
    return float(np.sum(np.diff(np.r_[0,tp])*tp/(ends+1))/yy.sum())


def predict_artifact(spec,rows):
    folder=Path(spec['folder']);condition=spec.get('condition',{})
    rr=history_shuffle(rows) if condition.get('shuffle') else rows
    if (folder/'model.joblib').is_file():return joblib.load(folder/'model.joblib').predict(rr)
    state=torch.load(folder/'model.pt',weights_only=True,map_location='cpu');prep=joblib.load(folder/'preparation.joblib')
    table=np.zeros((state['vocab'],32),np.float32) if state.get('frozen_vectors') else None
    model=BehaviorNet(state['vocab'],state['kind'],vectors=table,use_intervals=condition.get('use_intervals',True));model.load_state_dict(state['state_dict'])
    seq,dt=prepare(rr,prep['lookup']);return score_net(model,seq,dt,np.zeros((len(rr),0),np.float32))


def main():
    p=argparse.ArgumentParser();p.add_argument('--decision',required=True);p.add_argument('--output',required=True);a=p.parse_args()
    decision=json.loads(Path(a.decision).read_text())
    if decision['status']!='all_development_choices_frozen_before_final_test' or decision['final_test_previously_scored']:
        raise ValueError('Final evaluation not admitted')
    for path,h in decision['artifact_sha256'].items():
        if sha(path)!=h:raise ValueError('Frozen model/data/code changed '+path)
    out=Path(a.output);out.mkdir(parents=True,exist_ok=False);write(out/'decision.json',decision)
    rows,_=read_records(decision['records']);split=np.load(decision['split'],allow_pickle=False)
    dev=[rows[int(i)] for i in split['development']];test=[rows[int(i)] for i in split['test']];dy=np.array([r['is_fraud'] for r in dev]);y=np.array([r['is_fraud'] for r in test])
    df=Counter(x for i in split['train'] for x in set(events(rows[int(i)])))
    rare={name:np.array([any(low<=df[x]<=high for x in set(events(r))) for r in test]) for name,low,high in [('unseen',0,0),('rare1to5',1,5),('medium6to20',6,20)]}
    torch.set_num_threads(2);artifacts=decision['models'];evaluations={};test_scores={};dev_scores={}
    for key,spec in artifacts.items():
        ds=predict_artifact(spec,dev);original=np.load(Path(spec['folder'])/'development-scores.npy',allow_pickle=False)
        diff=float(np.max(np.abs(ds-original)))
        if diff>1e-6:raise ValueError('Complete independent development reload differs')
        tick=time.monotonic();cpu=time.process_time();score=predict_artifact(spec,test);elapsed=time.monotonic()-tick;CPU=time.process_time()-cpu
        threshold=development_threshold(dy,original);value=metrics(y,score,threshold)
        if abs(value['AP']-manual_ap(y,score))>1e-12:raise ValueError('Independent AP differs')
        value.update(test_scoring_plus_model_load_wall_seconds=elapsed,test_scoring_plus_model_load_CPU_seconds=CPU,development_reload_error=diff)
        value['rare_event_slices']={name:{'rows':int(mask.sum()),'positive':int(y[mask].sum()),'AP_descriptive_only':manual_ap(y[mask],score[mask]) if y[mask].sum() else None} for name,mask in rare.items()}
        folder=out/key;folder.mkdir(parents=True);np.save(folder/'test-scores.npy',score);write(folder/'evaluation.json',value)
        evaluations[key]=value;test_scores[key]=score;dev_scores[key]=original;print('final_scoring',key,'AP',value['AP'],flush=True)
    groups={}
    for key,spec in artifacts.items():groups.setdefault(spec['family'],[]).append(key)
    ensembles={}
    for name,keys in groups.items():
        score=np.mean([test_scores[k] for k in keys],axis=0);ds=np.mean([dev_scores[k] for k in keys],axis=0)
        ensembles[name]=metrics(y,score,development_threshold(dy,ds));np.save(out/(name+'-ensemble-test.npy'),score)
    result={'status':'all_frozen_models_final_static_evaluation_complete','instances':len(evaluations),'seed_evaluations':evaluations,'ensembles':ensembles,
        'decision_sha256':sha(a.decision),'post_test_tuning_allowed':False,'scope':'Static exact-sequence-grouped holdout, residual near-duplicate risk; not future/user-disjoint or daily business benefit','default_detector_changed':False,'paid_calls':0}
    write(out/'results.json',result)


if __name__=='__main__':main()
