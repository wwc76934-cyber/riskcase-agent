"""A3 fixed development-only comparisons. Never opens a final-test predictor."""
import argparse
import json
from pathlib import Path
import subprocess
import time
import joblib
import numpy as np
import torch
from app.risk.behavior_semantic.baselines import fit_net,prepare,score_net,BehaviorNet
from app.risk.behavior_semantic.semantics import history_shuffle
from app.risk.behavior_semantic.study import read_records,sha,write,metrics,development_threshold


def main():
    p=argparse.ArgumentParser();p.add_argument('--protocol',required=True);p.add_argument('--output',required=True);a=p.parse_args()
    policy=json.loads(Path(a.protocol).read_text());parent=Path(policy['A2_run'])
    if policy['status']!='registered_development_only_A3' or not (parent/'development-results.json').is_file():raise ValueError('A2 development fits must finish first')
    prior=json.loads((parent/'development-results.json').read_text())
    if prior['final_test_predictions_generated']:raise ValueError('Final test already exposed; refuse further selection')
    for f,h in policy['code_sha256'].items():
        if sha(f)!=h:raise ValueError('A3 source freeze changed '+f)
    out=Path(a.output);out.mkdir(parents=True,exist_ok=False)
    protocol={**policy,'actual_fitting_commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()};write(out/'protocol.json',protocol)
    rows,_=read_records(policy['records']);split=np.load(policy['split'],allow_pickle=False)
    if sha(policy['records'])!=policy['records_sha256'] or sha(policy['split'])!=policy['split_sha256']:raise ValueError('Data/split changed')
    train=[rows[int(i)] for i in split['train']];dev=[rows[int(i)] for i in split['development']]
    y=np.array([r['is_fraud'] for r in train]);dy=np.array([r['is_fraud'] for r in dev])
    bank=Path(policy['bank']);manifest=json.loads((bank/'manifest.json').read_text());tables={}
    for name,h in manifest['table_sha256'].items():
        if sha(bank/(name+'.npy'))!=h:raise ValueError('Representation bytes changed')
        tables[name]=np.load(bank/(name+'.npy'),allow_pickle=False)
    records={};evaluations={};artifacts={};begin=time.monotonic()
    for case in policy['conditions']:
        name=case['name'];tr=history_shuffle(train) if case['shuffle'] else train;dv=history_shuffle(dev) if case['shuffle'] else dev
        if case['shuffle'] and case['use_intervals']:raise ValueError('Shuffling must not invent an interval axis')
        for seed in case['seeds']:
            folder=out/name/f'seed-{seed}';folder.mkdir(parents=True);key=f'{name}/seed-{seed}'
            table=tables[case['table']] if case['table'] else None
            torch.manual_seed(seed);ordinary=BehaviorNet(1918,'gru')
            torch.manual_seed(seed);matched=BehaviorNet(1918,'gru',vectors=table,use_intervals=case['use_intervals'])
            for k,v in ordinary.state_dict().items():
                if k!='embedding.weight' and not torch.equal(v,matched.state_dict()[k]):raise ValueError('Non-vector initial parameters differ')
            model,lookup,prep,record=fit_net(tr,y,dv,dy,kind='gru',seed=seed,max_epochs=30,patience=5,vocab_ids=range(1916),vectors=table,use_intervals=case['use_intervals'],checkpoint_dir=folder)
            torch.save({'kind':'gru','vocab':1918,'state_dict':model.state_dict(),'condition':case,'frozen_vectors':table is not None},folder/'model.pt')
            joblib.dump({'lookup':lookup,'prep':prep},folder/'preparation.joblib')
            seq,dt=prepare(dv,lookup);scores=score_net(model,seq,dt,np.zeros((len(dv),0),np.float32))
            restored=BehaviorNet(1918,'gru',vectors=table,use_intervals=case['use_intervals']);restored.load_state_dict(torch.load(folder/'model.pt',weights_only=True)['state_dict'])
            replay=score_net(restored,seq,dt,np.zeros((len(dv),0),np.float32));difference=float(np.max(np.abs(scores-replay)))
            if difference>1e-6:raise ValueError('Full development model reload differs')
            if table is not None and not np.array_equal(model.embedding.weight.detach().numpy(),table):raise ValueError('Frozen table was updated')
            record.update(condition=case,downstream_initial_values_matched=True,model_sha256=sha(folder/'model.pt'),development_reload_error=difference)
            write(folder/'training.json',record);np.save(folder/'development-scores.npy',scores)
            evaluations[key]=metrics(dy,scores,development_threshold(dy,scores));records[key]=record;artifacts[key]={'folder':str(folder),'condition':case}
            print('A3',key,'development_AP',evaluations[key]['AP'],flush=True)
    ensembles={}
    for case in policy['conditions']:
        name=case['name'];score=np.mean([np.load(out/name/f'seed-{s}'/'development-scores.npy') for s in case['seeds']],axis=0)
        np.save(out/name/'ensemble-development.npy',score);ensembles[name]=metrics(dy,score,development_threshold(dy,score))
    write(out/'all-training-complete.json',{'instances':len(records),'artifacts':artifacts})
    result={'status':'A3_development_comparison_complete_test_still_sealed','records':records,'seed_evaluations':evaluations,'development_ensembles':ensembles,
        'A2_results_sha256':sha(parent/'development-results.json'),'bank_manifest_sha256':sha(bank/'manifest.json'),
        'wall_seconds':time.monotonic()-begin,'final_test_predictions_generated':False,'default_detector_changed':False,'paid_calls':0}
    write(out/'development-results.json',result);print(json.dumps({'status':result['status'],'development':ensembles}))


if __name__=='__main__':main()
