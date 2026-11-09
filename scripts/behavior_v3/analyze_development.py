"""Development-only, paired exact-sequence-cluster uncertainty; no test predictions."""
import argparse
import json
from pathlib import Path
import numpy as np
from sklearn.metrics import average_precision_score
from app.risk.behavior_semantic.study import read_records,event_key,write,sha,metrics,development_threshold


def weighted_ap(y,scores,weights):
    order=np.argsort(-scores,kind='stable');ss=scores[order];yy=y[order];ww=weights[order]
    ends=np.r_[np.flatnonzero(np.diff(ss)!=0),len(ss)-1];tp=np.cumsum(ww*yy)[ends];tot=np.cumsum(ww)[ends]
    return float(np.sum(np.diff(np.r_[0,tp])*np.divide(tp,tot,out=np.zeros_like(tp,dtype=float),where=tot>0))/tp[-1])


def cluster_interval(y,left,right,groups,repetitions=1000):
    _,inverse=np.unique(groups,return_inverse=True);n=int(inverse.max())+1
    rng=np.random.default_rng(20261104);values=[];skipped=0;left_values=[];right_values=[]
    for _ in range(repetitions):
        weights=np.bincount(rng.integers(0,n,n),minlength=n)[inverse]
        if not np.dot(y,weights):skipped+=1;continue
        a=weighted_ap(y,left,weights);b=weighted_ap(y,right,weights)
        values.append(b-a);left_values.append(a);right_values.append(b)
    return {'AP_difference':float(average_precision_score(y,right)-average_precision_score(y,left)),
        'paired_exact_sequence_cluster_95':np.percentile(values,[2.5,97.5]).tolist(),
        'control_AP_cluster_95':np.percentile(left_values,[2.5,97.5]).tolist(),
        'candidate_AP_cluster_95':np.percentile(right_values,[2.5,97.5]).tolist(),
        'bootstrap_repetitions':len(values),'zero_positive_draws_skipped':skipped,
        'limit':'Exact sequence clusters are not users; residual near-sequence correlation can make intervals optimistic; descriptive repeated-development comparison.'}


def main():
    p=argparse.ArgumentParser();p.add_argument('--A2',required=True);p.add_argument('--A3',required=True);p.add_argument('--output',required=True);a=p.parse_args()
    A2,A3=Path(a.A2),Path(a.A3);r2=json.loads((A2/'development-results.json').read_text());r3=json.loads((A3/'development-results.json').read_text())
    if r2['final_test_predictions_generated'] or r3['final_test_predictions_generated']:raise ValueError('Test already exposed')
    policy=json.loads((A2/'protocol.json').read_text());rows,_=read_records(policy['records']);split=np.load(policy['split'],allow_pickle=False)
    dev=[rows[int(i)] for i in split['development']];y=np.array([r['is_fraud'] for r in dev]);groups=[event_key(r) for r in dev]
    scores={};seed_scores={};summary={}
    for run,result in [(A2,r2),(A3,r3)]:
        for file in run.glob('*/ensemble-development.npy'):
            name=file.parent.name;scores[name]=np.load(file,allow_pickle=False)
            summary[name]=metrics(y,scores[name],development_threshold(y,scores[name]))
        for file in run.glob('*/seed-*/development-scores.npy'):seed_scores[file.parent.parent.name+'/'+file.parent.name]=np.load(file,allow_pickle=False)
    comparisons={}
    pairs=[('numeric_tree','combined_lr'),('numeric_tree','tfidf_lr'),('numeric_tree','tfidf_svm'),('numeric_tree','gru'),('numeric_tree','transformer'),
        ('gru','qwen'),('random','qwen'),('tfidf_svd','qwen'),('combined_lr','qwen')]
    for old,new in pairs:comparisons[new+'_minus_'+old]=cluster_interval(y,scores[old],scores[new],groups)
    single_pairs=[('qwen/seed-42','description_shuffle/seed-42'),('gru/seed-42','learned_nointerval/seed-42'),('qwen/seed-42','qwen_nointerval/seed-42'),
        ('learned_nointerval/seed-42','learned_order_shuffle_nointerval/seed-42'),('qwen_nointerval/seed-42','qwen_order_shuffle_nointerval/seed-42')]
    for old,new in single_pairs:
        value=cluster_interval(y,seed_scores[old],seed_scores[new],groups);value['single_seed_diagnostic_not_stability_proof']=True
        comparisons[new+'_minus_'+old]=value
    seed_deltas={}
    for control in ('gru','random','tfidf_svd'):
        seed_deltas[control]=[float(average_precision_score(y,seed_scores[f'qwen/seed-{s}'])-average_precision_score(y,seed_scores[f'{control}/seed-{s}'])) for s in (42,43,44)]
    positive=all(comparisons['qwen_minus_'+control]['paired_exact_sequence_cluster_95'][0]>0 for control in ('gru','random','tfidf_svd','combined_lr')) and all(min(v)>0 for v in seed_deltas.values())
    result={'status':'development_decision_evidence_complete_test_still_sealed','development_summary':summary,'comparisons':comparisons,'Qwen_same_seed_AP_deltas':seed_deltas,
        'A4_status':'manual_convergence_and_correlation_review_required' if positive else 'not_admitted_no_credible_semantic_increment',
        'automatic_fusion_or_LoRA_allowed':False,'final_test_predictions_generated':False,
        'A2_source_sha256':sha(A2/'development-results.json'),'A3_source_sha256':sha(A3/'development-results.json')}
    out=Path(a.output);out.mkdir(parents=True,exist_ok=False);write(out/'report.json',result);print(json.dumps({'status':result['status'],'A4_status':result['A4_status']}))


if __name__=='__main__':main()
